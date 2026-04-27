"""Built-in admin auth: argon2 password + HS256 session cookie.

State (password hash, JWT signing secret) lives in the ``settings`` table so
the install wizard (M1.5) can manage it without touching disk-formatted files.

This module is deliberately small. OIDC / WebAuthn from ``config.auth.*`` are
declared in the schema but not yet consumed.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .models import Setting

SESSION_COOKIE = "seenoevil_session"
SESSION_TTL = timedelta(hours=12)
JWT_ALG = "HS256"

_PWD_KEY = "auth.admin_password_hash"
_EMAIL_KEY = "auth.admin_email"
_JWT_KEY = "auth.jwt_secret"

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
    """Hash and store the admin password. Idempotent."""
    if not password or len(password) < 8:
        raise ValueError("password must be at least 8 characters")
    _set_setting(session, _PWD_KEY, _hasher.hash(password))
    _set_setting(session, _EMAIL_KEY, email)


def admin_is_configured(session: Session) -> bool:
    return bool(_get_setting(session, _PWD_KEY))


def verify_admin(session: Session, email: str, password: str) -> bool:
    stored_email = _get_setting(session, _EMAIL_KEY)
    stored_hash = _get_setting(session, _PWD_KEY)
    if not stored_email or not stored_hash:
        return False
    if email.strip().lower() != str(stored_email).strip().lower():
        return False
    try:
        _hasher.verify(stored_hash, password)
    except VerifyMismatchError:
        return False
    return True


# ---------------------------------------------------------------------------
# Session cookies
# ---------------------------------------------------------------------------


def issue_session(session: Session, response: Response, email: str) -> str:
    secret = _ensure_jwt_secret(session)
    now = datetime.now(UTC)
    payload = {
        "sub": email,
        "iat": int(now.timestamp()),
        "exp": int((now + SESSION_TTL).timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=JWT_ALG)
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=False,  # Caddy terminates TLS in front; behind proxy this is fine.
        path="/",
    )
    return token


def clear_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")


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
        return str(sub)

    return _dep
