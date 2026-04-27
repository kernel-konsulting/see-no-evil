"""Panic-relax mode helpers.

Panic-relax is a temporary, global allow override that an admin can flip when
a profile or schedule is misbehaving and they need traffic flowing now (e.g.
during a remote-work crisis or when a kid genuinely needs an unblocked site
for homework). It is stored as a single ``Setting`` row keyed
``"panic_relax"`` so it survives restarts and is auditable.

The shape of the persisted JSON value::

    {
        "active": true,
        "until":  "2026-04-27T18:30:00+00:00" | null,
        "reason": "homework",
        "set_by": "admin@example.local",
        "set_at": "2026-04-27T17:30:00+00:00"
    }

A status with ``active = true`` but a past ``until`` is treated as inactive
without rewriting the row (the next admin write will clear it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from .models import Setting

PANIC_KEY = "panic_relax"


@dataclass(frozen=True)
class PanicState:
    active: bool
    until: datetime | None = None
    reason: str = ""
    set_by: str | None = None
    set_at: datetime | None = None


def _parse(value: dict | None) -> PanicState:
    if not value or not value.get("active"):
        return PanicState(active=False)
    until_s = value.get("until")
    until = datetime.fromisoformat(until_s) if until_s else None
    if until is not None and until <= datetime.now(UTC):
        return PanicState(active=False)
    set_at_s = value.get("set_at")
    set_at = datetime.fromisoformat(set_at_s) if set_at_s else None
    return PanicState(
        active=True,
        until=until,
        reason=str(value.get("reason") or ""),
        set_by=value.get("set_by"),
        set_at=set_at,
    )


def get_state(session: Session) -> PanicState:
    row = session.get(Setting, PANIC_KEY)
    return _parse(row.value if row else None)


def set_active(
    session: Session,
    *,
    duration_minutes: int,
    reason: str,
    set_by: str,
) -> PanicState:
    now = datetime.now(UTC)
    until = now + timedelta(minutes=duration_minutes)
    payload = {
        "active": True,
        "until": until.isoformat(),
        "reason": reason,
        "set_by": set_by,
        "set_at": now.isoformat(),
    }
    row = session.get(Setting, PANIC_KEY)
    if row is None:
        session.add(Setting(key=PANIC_KEY, value=payload))
    else:
        row.value = payload
    session.commit()
    return _parse(payload)


def clear(session: Session) -> None:
    row = session.get(Setting, PANIC_KEY)
    if row is None:
        return
    row.value = {"active": False}
    session.commit()
