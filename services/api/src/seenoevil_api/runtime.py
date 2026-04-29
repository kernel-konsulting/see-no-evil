"""Runtime settings persisted in the ``settings`` table.

Everything in here is editable at runtime via the admin UI; the proxy polls
``GET /v1/runtime`` every few seconds and applies the values without a restart.

Defaults are conservative: every inspector is on, no extra global allow/deny
entries beyond what profiles define, notifications off until configured.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import Setting

# Settings keys (all stored as JSON in the ``settings`` table).
KEY = "runtime.v1"

DEFAULTS: dict[str, Any] = {
    "inspect": {
        "image": True,
        "video": True,
        "text": True,
        "domain": True,
        "url": True,
    },
    "lists": {
        "global_allow_domains": [],
        "enforce_global_allowlist": False,
        "global_deny_domains": [],
        "global_deny_keywords": [],
    },
    "text": {
        "nsfw_threshold": 0.5,
    },
    "image": {
        # These default below the classifier's built-in defaults
        # (sexy=0.8, porn/hentai=0.5) so lingerie / swimwear pages get
        # caught more aggressively. Profile thresholds still take precedence.
        "sexy_threshold": 0.6,
        "porn_threshold": 0.5,
        "hentai_threshold": 0.5,
    },
    "notifications": {
        "enabled": False,
        "ntfy_url": "",
        "webhook_url": "",
        "webhook_token": "",
        "on_block": True,
        "on_quarantine": True,
        "on_panic": True,
    },
}


def _deep_merge(base: dict[str, Any], over: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def get_runtime(session: Session) -> dict[str, Any]:
    row = session.get(Setting, KEY)
    stored = row.value if row else {}
    return _deep_merge(DEFAULTS, stored if isinstance(stored, dict) else {})


def update_runtime(session: Session, patch: dict[str, Any]) -> dict[str, Any]:
    current = get_runtime(session)
    merged = _deep_merge(current, patch)
    row = session.get(Setting, KEY)
    if row is None:
        session.add(Setting(key=KEY, value=merged))
    else:
        row.value = merged
    session.commit()
    return merged
