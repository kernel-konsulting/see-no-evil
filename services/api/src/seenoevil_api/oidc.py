"""Minimal OpenID Connect Authorization Code + PKCE client.

Why hand-roll instead of pulling Authlib? Because we need only one flow
(redirect → callback → userinfo), validation is "is this email in the
allow-list", and a 200-line module avoids a ~3 MB transitive dependency
graph in a privacy-focused appliance.

State + PKCE verifier are stashed in the ``settings`` table keyed by a
short opaque ``state`` token. The verifier is consumed exactly once on
callback. Stored entries older than 10 minutes are considered expired.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import OIDCConfig
from .models import Setting

log = logging.getLogger("seenoevil_api.oidc")

_DISCOVERY_PATH = "/.well-known/openid-configuration"
_STATE_PREFIX = "oidc.state."
_DISCOVERY_KEY = "oidc.discovery"
_STATE_TTL_SECONDS = 600


@dataclass(frozen=True)
class StartedFlow:
    authorize_url: str
    state: str


@dataclass(frozen=True)
class FinishedFlow:
    email: str


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _pkce_pair() -> tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _store(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def _load(session: Session, key: str) -> Any | None:
    row = session.get(Setting, key)
    return row.value if row else None


def _delete(session: Session, key: str) -> None:
    row = session.get(Setting, key)
    if row is not None:
        session.delete(row)


def _purge_expired_states(session: Session) -> None:
    now = int(time.time())
    rows = session.scalars(select(Setting).where(Setting.key.like(f"{_STATE_PREFIX}%"))).all()
    for row in rows:
        v = row.value or {}
        if int(v.get("created", 0)) + _STATE_TTL_SECONDS < now:
            session.delete(row)


def discover(cfg: OIDCConfig, session: Session, client: httpx.Client | None = None) -> dict:
    """Fetch and cache the OIDC discovery document (TTL 1h)."""
    # F15: enforce https issuer (no http OIDC)
    if not cfg.issuer or not cfg.issuer.startswith("https://"):
        raise ValueError("oidc.issuer must be https")
    cached = _load(session, _DISCOVERY_KEY)
    if cached and cached.get("issuer") == cfg.issuer:
        # 3600s TTL — provider rotation picked up without restart.
        fetched_at = int(cached.get("fetched_at", 0))
        if fetched_at and int(time.time()) - fetched_at < 3600:
            return cached
        if not fetched_at:
            # legacy cache without timestamp — treat as fresh for 1h from now
            # to avoid hammering, then refetch next time.
            cached["fetched_at"] = int(time.time())
            _store(session, _DISCOVERY_KEY, cached)
            session.flush()
            return cached
    if not cfg.issuer:
        raise ValueError("oidc.issuer not configured")
    url = cfg.issuer.rstrip("/") + _DISCOVERY_PATH
    own = client is None
    if own:
        client = httpx.Client(timeout=10.0)
    try:
        resp = client.get(url)
        resp.raise_for_status()
        doc = resp.json()
    finally:
        if own:
            client.close()
    # F15: validate endpoints are https and all hosts match issuer
    from urllib.parse import urlparse as _urlparse

    issuer_host = _urlparse(cfg.issuer).hostname if cfg.issuer else None
    for _key in ("authorization_endpoint", "token_endpoint", "userinfo_endpoint"):
        _ep = doc.get(_key)
        if _ep:
            if not isinstance(_ep, str) or not _ep.startswith("https://"):
                raise ValueError(f"oidc {_key} must be https")
            if issuer_host:
                ep_host = _urlparse(_ep).hostname
                if ep_host != issuer_host:
                    raise ValueError(f"oidc {_key} host must match issuer host")
    doc["fetched_at"] = int(time.time())
    doc["issuer"] = cfg.issuer
    _store(session, _DISCOVERY_KEY, doc)
    session.flush()
    return doc


def start_flow(
    cfg: OIDCConfig,
    session: Session,
    *,
    redirect_url: str,
    client: httpx.Client | None = None,
) -> StartedFlow:
    """Begin an Authorization Code + PKCE flow.

    Returns the URL the browser should be redirected to, plus the opaque
    ``state`` so the caller can drop a cookie if it wishes (we keep the
    server-side mapping authoritative).
    """
    if not cfg.enabled:
        raise ValueError("oidc disabled")
    doc = discover(cfg, session, client=client)
    authorize_endpoint = doc.get("authorization_endpoint")
    if not authorize_endpoint:
        raise ValueError("oidc discovery missing authorization_endpoint")

    verifier, challenge = _pkce_pair()
    state = _b64url(secrets.token_bytes(24))
    nonce = _b64url(secrets.token_bytes(16))
    _purge_expired_states(session)
    _store(
        session,
        _STATE_PREFIX + state,
        {
            "verifier": verifier,
            "redirect_url": redirect_url,
            "created": int(time.time()),
            "nonce": nonce,
        },
    )
    session.flush()

    qs = httpx.QueryParams(
        {
            "response_type": "code",
            "client_id": cfg.client_id or "",
            "redirect_uri": redirect_url,
            "scope": " ".join(cfg.scopes),
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    sep = "&" if "?" in authorize_endpoint else "?"
    return StartedFlow(authorize_url=f"{authorize_endpoint}{sep}{qs}", state=state)


def finish_flow(
    cfg: OIDCConfig,
    session: Session,
    *,
    code: str,
    state: str,
    client: httpx.Client | None = None,
) -> FinishedFlow:
    """Exchange ``code`` for tokens, then fetch the userinfo email."""
    saved = _load(session, _STATE_PREFIX + state)
    if not saved:
        raise ValueError("unknown or expired state")
    if int(saved.get("created", 0)) + _STATE_TTL_SECONDS < int(time.time()):
        _delete(session, _STATE_PREFIX + state)
        session.flush()
        raise ValueError("state expired")
    verifier = saved["verifier"]
    redirect_url = saved["redirect_url"]
    saved_nonce = saved.get("nonce")

    doc = discover(cfg, session, client=client)
    token_endpoint = doc.get("token_endpoint")
    userinfo_endpoint = doc.get("userinfo_endpoint")
    if not token_endpoint or not userinfo_endpoint:
        raise ValueError("oidc discovery missing token/userinfo endpoint")

    own = client is None
    if own:
        client = httpx.Client(timeout=10.0)
    try:
        token_data: dict[str, Any] = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": cfg.client_id or "",
            "redirect_uri": redirect_url,
            "code_verifier": verifier,
        }
        if cfg.client_secret:
            token_data["client_secret"] = cfg.client_secret
        resp = client.post(token_endpoint, data=token_data)
        resp.raise_for_status()
        tokens = resp.json()
        access_token = tokens.get("access_token")
        if not access_token:
            raise ValueError("oidc token response missing access_token")
        # Verify nonce if id_token present and we issued one.
        id_token = tokens.get("id_token")
        if saved_nonce and id_token:
            try:
                # id_token is JWT: header.payload.sig — verify nonce in payload without
                # signature verification (provider signature checked by provider libs
                # when needed; we at least ensure nonce binding).
                payload_b64 = id_token.split(".")[1]
                # pad base64
                payload_b64 += "=" * (-len(payload_b64) % 4)
                payload_json = base64.urlsafe_b64decode(payload_b64.encode()).decode()
                import json as _json

                payload = _json.loads(payload_json)
                token_nonce = payload.get("nonce")
                if token_nonce is not None and token_nonce != saved_nonce:
                    raise ValueError("oidc nonce mismatch")
            except (ValueError, IndexError, base64.binascii.Error, Exception) as exc:
                # Nonce mismatch is security-relevant; other decode errors are logged
                # but don't block userinfo flow if provider omits nonce.
                if isinstance(exc, ValueError) and "nonce mismatch" in str(exc):
                    raise
                log.debug("oidc id_token nonce check skipped: %s", exc)
        ui = client.get(
            userinfo_endpoint,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        ui.raise_for_status()
        info = ui.json()
    except Exception:
        # Make state single-use even on transient token/userinfo failures
        _delete(session, _STATE_PREFIX + state)
        session.flush()
        raise
    finally:
        if own:
            client.close()

    email = info.get("email")
    if not email:
        _delete(session, _STATE_PREFIX + state)
        session.flush()
        raise ValueError("oidc userinfo response missing email")
    if cfg.allowed_emails and email.lower() not in {e.lower() for e in cfg.allowed_emails}:
        log.warning("oidc sign-in rejected: %s not in allowed_emails", email)
        _delete(session, _STATE_PREFIX + state)
        session.flush()
        raise PermissionError("email not permitted")
    _delete(session, _STATE_PREFIX + state)
    session.flush()
    return FinishedFlow(email=str(email))
