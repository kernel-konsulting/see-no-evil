"""HMAC-SHA256 signing of audit rows (tamper detection).

Every ``AuditDecision`` row is signed with a per-install secret stored in the
``settings`` table (generated on first use). The signature covers the row's
substantive fields; the audit list endpoint recomputes it and reports
``signature_valid`` so a tampered audit log is detectable rather than silent.

This is a detectability control, not an append-only guarantee — an attacker
with DB write access and the signing secret can re-sign rows, which is why the
secret lives in the same DB. It raises the bar against casual tampering
(e.g. editing the SQLite file to scrub browsing history).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from datetime import UTC

from sqlalchemy.orm import Session

from .models import AuditDecision, Setting

_SIG_KEY = "audit.hmac_secret"
_HEX_LEN = 64


def ensure_secret(session: Session) -> str:
    """Return the per-install audit signing secret, generating it on first use."""
    row = session.get(Setting, _SIG_KEY)
    if row is not None and isinstance(row.value, str) and row.value:
        return row.value
    secret = hashlib.sha256(secrets.token_bytes(48)).hexdigest()
    session.add(Setting(key=_SIG_KEY, value=secret))
    session.flush()
    return secret


def _canonical(secret: str, row: AuditDecision) -> str:
    """Deterministic string covering every substantive field of an audit row.

    ``id``/``ts`` are covered too, so swapping rows or rewriting timestamps is
    detected. classifier_scores is JSON-serialized with sorted keys so the
    same logical dict always canonicalizes identically. ``ts`` is normalized
    to naive UTC: SQLite drops tzinfo on read-back, so an aware timestamp
    must canonicalize identically to its naive round-trip.
    """
    ts = row.ts
    if ts is not None:
        if ts.tzinfo is not None:
            ts = ts.astimezone(UTC).replace(tzinfo=None)
        ts_str = ts.isoformat()
    else:
        ts_str = ""
    # Include thumbnail hash, not just presence, so swapping preview is
    # detectable. Use sha256 hex to keep canonical short.
    thumb_hash = hashlib.sha256(row.thumbnail_b64.encode()).hexdigest() if row.thumbnail_b64 else ""
    parts = [
        str(row.id),
        ts_str,
        str(row.device_id),
        str(row.profile_id),
        row.url or "",
        row.content_type or "",
        row.decision or "",
        row.reason or "",
        json.dumps(row.classifier_scores or {}, sort_keys=True, separators=(",", ":")),
        thumb_hash,
    ]
    return "|".join(parts)


def sign_row(session: Session, row: AuditDecision) -> str:
    secret = ensure_secret(session)
    sig = hmac.new(secret.encode(), _canonical(secret, row).encode(), hashlib.sha256).hexdigest()
    row.signature = sig
    return sig


def _canonical_legacy(secret: str, row: AuditDecision) -> str:
    """Legacy canonical (presence bit) for verification during upgrade."""
    ts = row.ts
    if ts is not None:
        if ts.tzinfo is not None:
            ts = ts.astimezone(UTC).replace(tzinfo=None)
        ts_str = ts.isoformat()
    else:
        ts_str = ""
    parts = [
        str(row.id),
        ts_str,
        str(row.device_id),
        str(row.profile_id),
        row.url or "",
        row.content_type or "",
        row.decision or "",
        row.reason or "",
        json.dumps(row.classifier_scores or {}, sort_keys=True, separators=(",", ":")),
        "1" if row.thumbnail_b64 else "0",
    ]
    return "|".join(parts)


def verify_row(secret: str, row: AuditDecision) -> bool:
    """Recompute the signature for ``row`` and compare (constant-time)."""
    if not row.signature:
        return False
    expected = hmac.new(
        secret.encode(), _canonical(secret, row).encode(), hashlib.sha256
    ).hexdigest()
    if hmac.compare_digest(expected, row.signature):
        return True
    # Fallback to legacy presence-bit canonical for rows signed before upgrade.
    legacy = hmac.new(
        secret.encode(), _canonical_legacy(secret, row).encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(legacy, row.signature)
