"""Built-in admin auth: argon2 password + HS256 session cookie.

State (password hash, JWT signing secret) lives in the ``settings`` table so
the install wizard (M1.5) can manage it without touching disk-formatted files.

This module is deliberately small. OIDC / WebAuthn from ``config.auth.*`` are
declared in the schema but not yet consumed.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import AppConfig
from .models import Setting, User

log = logging.getLogger("seenoevil_api.auth")

SESSION_COOKIE = "seenoevil_session"
# 4h reduces window after password change / disable; was 12h (#34).
SESSION_TTL = timedelta(hours=4)
JWT_ALG = "HS256"
# Double-submit CSRF cookie/header names (utility, not yet wired globally).
CSRF_COOKIE = "seenoevil_csrf"
CSRF_HEADER = "x-csrf-token"

_PWD_KEY = "auth.admin_password_hash"
_EMAIL_KEY = "auth.admin_email"
_JWT_KEY = "auth.jwt_secret"

# Recognised role values. ``viewer`` users get read-only access to the audit
# log + quarantine queue and may flag items as false positives, but cannot
# mutate policy/devices/profiles or allow/deny quarantine items.
ROLE_ADMIN = "admin"
ROLE_VIEWER = "viewer"
VALID_ROLES = frozenset({ROLE_ADMIN, ROLE_VIEWER})

_hasher = PasswordHasher()


# ---------------------------------------------------------------------------
# Setting helpers
# ---------------------------------------------------------------------------


def _get_setting(session: Session, key: str) -> Any | None:
    row = session.get(Setting, key)
    return row.value if row else None


def _set_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def _ensure_jwt_secret(session: Session) -> str:
    secret = _get_setting(session, _JWT_KEY)
    if not secret:
        secret = secrets.token_urlsafe(48)
        _set_setting(session, _JWT_KEY, secret)
        session.flush()
    return secret


# ---------------------------------------------------------------------------
# Password management
# ---------------------------------------------------------------------------


def set_admin_password(session: Session, email: str, password: str) -> None:
    """Hash and store the admin password. Idempotent.

    Writes to BOTH the legacy settings rows (so older lookups still work) and
    the new ``users`` table (so multi-user features see the bootstrap admin).
    """
    norm = email.strip().lower()
    pwd_hash = hash_password(password)
    _set_setting(session, _PWD_KEY, pwd_hash)
    _set_setting(session, _EMAIL_KEY, norm)
    user = session.scalars(select(User).where(User.email == norm)).first()
    if user is None:
        session.add(User(email=norm, password_hash=pwd_hash, role=ROLE_ADMIN))
    else:
        user.password_hash = pwd_hash
        user.role = ROLE_ADMIN
        user.disabled = False


def admin_is_configured(session: Session) -> bool:
    if _get_setting(session, _PWD_KEY):
        return True
    return session.scalars(select(User).limit(1)).first() is not None


def verify_admin(session: Session, email: str, password: str) -> bool:
    norm = email.strip().lower()
    # Prefer the multi-user table. If a User row exists we must NOT fall
    # back to the legacy settings row — otherwise a disabled admin could
    # still log in via the old hash (#6, #7).
    user = session.scalars(select(User).where(User.email == norm)).first()
    if user is not None:
        if user.disabled:
            return False
        try:
            _hasher.verify(user.password_hash, password)
            return True
        except VerifyMismatchError:
            return False
    # Fallback: legacy single-admin row in the settings table.
    stored_email = _get_setting(session, _EMAIL_KEY)
    stored_hash = _get_setting(session, _PWD_KEY)
    if not stored_email or not stored_hash:
        return False
    if norm != str(stored_email).strip().lower():
        return False
    try:
        _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    return True


# ---------------------------------------------------------------------------
# Multi-user helpers
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    return _hasher.hash(password)


def list_users(session: Session) -> list[User]:
    return list(session.scalars(select(User).order_by(User.email)).all())


def create_user(session: Session, email: str, password: str, role: str = ROLE_ADMIN) -> User:
    norm = email.strip().lower()
    if not norm or "@" not in norm:
        raise ValueError("email must contain '@'")
    if role not in VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
    if session.scalars(select(User).where(User.email == norm)).first() is not None:
        raise ValueError("user already exists")
    user = User(email=norm, password_hash=hash_password(password), role=role)
    session.add(user)
    session.commit()
    return user


def update_user(
    session: Session,
    user_id: int,
    *,
    password: str | None = None,
    role: str | None = None,
    disabled: bool | None = None,
) -> User:
    user = session.get(User, user_id)
    if user is None:
        raise ValueError("user not found")
    if password is not None:
        user.password_hash = hash_password(password)
    if role is not None:
        if role not in VALID_ROLES:
            raise ValueError(f"role must be one of {sorted(VALID_ROLES)}")
        user.role = role
    if disabled is not None:
        user.disabled = disabled
    session.commit()
    return user


def delete_user(session: Session, user_id: int) -> None:
    user = session.get(User, user_id)
    if user is None:
        return
    session.delete(user)
    session.commit()


# ---------------------------------------------------------------------------
# Session cookies
# ---------------------------------------------------------------------------


def _secure_cookies(request: Request | None = None) -> bool:
    """Whether session/csrf cookies should be marked Secure.

    In production Caddy terminates TLS and all admin traffic is https, so
    Secure must be True. For local http dev without TLS, set
    SNE_SECURE_COOKIES=0 or pass an http Request.
    """
    env = os.environ.get("SNE_SECURE_COOKIES", "").lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    if request is not None:
        return request.url.scheme == "https"
    # Default: secure in prod (even behind Caddy, the browser still sees https).
    return True


def issue_session(
    session: Session, response: Response, email: str, request: Request | None = None
) -> str:
    secret = _ensure_jwt_secret(session)
    now = datetime.now(UTC)
    payload = {
        "sub": email,
        "iat": int(now.timestamp()),
        "exp": int((now + SESSION_TTL).timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=JWT_ALG)
    secure = _secure_cookies(request)
    # Caddy terminates TLS in front; browser still sees https, so Secure=True.
    # samesite=strict is strongest for admin session; lax would allow top-level
    # GET navigations but strict prevents CSRF more fully (combined with
    # double-submit token below).
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )
    # Double-submit CSRF cookie (readable by JS, compared to X-CSRF-Token header).
    csrf = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )
    return token


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def issue_csrf_token(response: Response, request: Request | None = None) -> str:
    """Set a fresh CSRF double-submit cookie and return its value."""
    secure = _secure_cookies(request)
    token = secrets.token_urlsafe(32)
    response.set_cookie(
        key=CSRF_COOKIE,
        value=token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )
    return token


def verify_csrf(request: Request) -> None:
    """Double-submit CSRF check for state-changing methods (utility).

    Compares the CSRF_COOKIE (set by issue_session) with the X-CSRF-Token
    header. Not yet wired globally; routers can call this in dependencies.
    """
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        cookie_token = request.cookies.get(CSRF_COOKIE)
        header_token = request.headers.get(CSRF_HEADER) or request.headers.get("x-csrf-token")
        if not cookie_token or not header_token:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf token missing")
        if not hmac.compare_digest(cookie_token, header_token):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "csrf validation failed")


def _decode(session: Session, token: str) -> dict[str, Any]:
    secret = _get_setting(session, _JWT_KEY)
    if not secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "no session secret configured")
    try:
        return jwt.decode(token, secret, algorithms=[JWT_ALG])
    except jwt.PyJWTError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session") from exc


def require_admin_factory(get_session_dep):
    """Build a FastAPI dependency that requires a valid admin session cookie.

    ``get_session_dep`` is the per-request DB-session dependency from ``db.get_db``.
    """

    def _dep(
        session: Session = Depends(get_session_dep),
        token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> str:
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
        payload = _decode(session, token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session")
        # Block non-admin roles and disabled accounts. Legacy single-admin
        # (no User row) defaults to admin.
        user = session.scalars(select(User).where(User.email == str(sub).lower())).first()
        if user is not None and (user.disabled or user.role != ROLE_ADMIN):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "user disabled" if user.disabled else "admin role required",
            )
        return str(sub)

    return _dep


def current_user_factory(get_session_dep):
    """Build a dependency that returns the current logged-in user (any role).

    Returns a ``(email, role)`` tuple. Callers that only need to know the
    user is authenticated can ignore ``role``.
    """

    def _dep(
        session: Session = Depends(get_session_dep),
        token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    ) -> tuple[str, str]:
        if not token:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not authenticated")
        payload = _decode(session, token)
        sub = payload.get("sub")
        if not sub:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session")
        email = str(sub).lower()
        user = session.scalars(select(User).where(User.email == email)).first()
        if user is None:
            # Legacy single-admin path: treat as admin.
            return (email, ROLE_ADMIN)
        if user.disabled:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "user disabled")
        return (user.email, user.role)

    return _dep


def require_user_factory(get_session_dep):
    """Build a dependency that requires *any* authenticated user."""

    inner = current_user_factory(get_session_dep)

    def _dep(current: tuple[str, str] = Depends(inner)) -> tuple[str, str]:
        return current

    return _dep


# ---------------------------------------------------------------------------
# In-memory rate limiting (login / setup brute-force protection)
# ---------------------------------------------------------------------------


class _RateLimiter:
    """Sliding-window rate limiter keyed by arbitrary strings (e.g. client IP).

    In-memory only: fine for a single-API-process pod; each process keeps its
    own window, which is acceptable for LAN-scale deployments.
    """

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = [t for t in self._hits.get(key, []) if now - t < self.window]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


# 10 login attempts / 5 min per source IP; 5 setup attempts / 5 min.
_login_limiter = _RateLimiter(limit=10, window_seconds=300)
_setup_limiter = _RateLimiter(limit=5, window_seconds=300)


def clear_rate_limiters() -> None:
    """Reset all limiter state (used by tests between cases)."""
    _login_limiter.clear()
    _setup_limiter.clear()


# Hosts that are trusted to set X-Forwarded-For (loopback from Caddy / pod).
_TRUSTED_PROXY_HOSTS = frozenset({"127.0.0.1", "::1", "10.0.0.1", "::ffff:127.0.0.1"})


def client_ip(request: Request) -> str:
    host = request.client.host if request.client else "unknown"
    # Only trust X-Forwarded-For when the direct peer is a known loopback/trusted
    # proxy (Caddy on 127.0.0.1). Otherwise a LAN attacker could spoof any IP
    # and bypass per-IP rate limits (#19).
    xff = request.headers.get("x-forwarded-for")
    if xff and host in _TRUSTED_PROXY_HOSTS:
        first = xff.split(",")[0].strip()
        if first:
            return first
    return host


def check_rate_limit(limiter: _RateLimiter, request: Request) -> None:
    if not limiter.allow(client_ip(request)):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "too many attempts, try again later",
        )


# ---------------------------------------------------------------------------
# Internal service authentication (proxy / scanner bearer tokens)
# ---------------------------------------------------------------------------


def _bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer ") :].strip()
    return ""


def _token_matches(provided: str | None, expected: str | None) -> bool:
    return bool(provided and expected and hmac.compare_digest(provided, expected))


def require_proxy_factory(get_config):
    """Dependency: only the in-pod proxy may call this endpoint.

    Authenticates `Authorization: Bearer <token>` against
    ``proxy.api_token`` (or the SEENOEVIL_PROXY_TOKEN env var). Fails closed:
    if no token is configured at all, the endpoint refuses to serve rather
    than silently accepting unauthenticated LAN traffic.
    """

    def _dep(
        request: Request,
        config: AppConfig = Depends(get_config),
    ) -> str:
        expected = config.proxy.api_token or os.environ.get("SEENOEVIL_PROXY_TOKEN")
        if not expected:
            log.warning("proxy token not configured — failing closed on %s", request.url.path)
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "proxy token not configured",
            )
        if not _token_matches(_bearer_token(request), expected):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid proxy token")
        return "proxy"

    return _dep


def require_admin_or_scanner_factory(get_session_dep, get_config):
    """Dependency: a valid admin session cookie, or the scanner's bearer token.

    Used by /v1/devices/discover so the scanner can report findings without a
    browser session while LAN clients still cannot.
    """

    def _dep(
        request: Request,
        session: Session = Depends(get_session_dep),
        token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
        config: AppConfig = Depends(get_config),
    ) -> str:
        if token:
            try:
                payload = _decode(session, token)
                sub = payload.get("sub")
                if not sub:
                    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid session")
                user = session.scalars(select(User).where(User.email == str(sub).lower())).first()
                if user is not None and (user.disabled or user.role != ROLE_ADMIN):
                    raise HTTPException(
                        status.HTTP_403_FORBIDDEN,
                        "user disabled" if user.disabled else "admin role required",
                    )
                return str(sub)
            except HTTPException as exc:
                # Do not swallow 403 (disabled / viewer): those must remain forbidden
                # and must NOT fall through to the scanner token, otherwise a
                # disabled admin could still call the endpoint via a scanner token.
                if exc.status_code == status.HTTP_403_FORBIDDEN:
                    raise
                # 401 (invalid/expired cookie) -> fall through to scanner token check.
                pass
        expected = config.scanner.api_token or os.environ.get("SCANNER_API_TOKEN")
        if not expected:
            log.warning("scanner token not configured — failing closed on %s", request.url.path)
        if not _token_matches(_bearer_token(request), expected):
            raise HTTPException(
                status.HTTP_401_UNAUTHORIZED, "admin session or scanner token required"
            )
        return "scanner"

    return _dep
