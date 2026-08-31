"""Retention cleanup for the audit log and resolved quarantine items.

The audit table (which can carry a base64 thumbnail per row) would otherwise
grow without bound — observability.audit.retention_days is enforced here.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from .models import AuditDecision, QuarantineItem

log = logging.getLogger("seenoevil_api.cleanup")

_CLEANUP_INTERVAL = 24 * 3600  # once a day


def cleanup_expired(session: Session, retention_days: int) -> dict[str, int]:
    """Delete audit rows older than retention_days and stale quarantine rows.

    Quarantine: only *resolved* items (allowed/denied) are pruned; pending
    items are kept regardless of age so nothing awaiting review vanishes.
    """
    if retention_days <= 0:
        return {"audit": 0, "quarantine": 0}
    cutoff = datetime.now(UTC) - timedelta(days=retention_days)
    audit_deleted = 0
    quarantine_deleted = 0

    def _batched_audit() -> int:
        total = 0
        while True:
            ids = list(
                session.scalars(
                    select(AuditDecision.id).where(AuditDecision.ts < cutoff).limit(1000)
                )
            )
            if not ids:
                break
            res = session.execute(delete(AuditDecision).where(AuditDecision.id.in_(ids)))
            total += res.rowcount or len(ids)
            session.commit()
        return total

    def _batched_quarantine() -> int:
        total = 0
        while True:
            ids = list(
                session.scalars(
                    select(QuarantineItem.id)
                    .where(
                        QuarantineItem.status != "pending",
                        QuarantineItem.resolved_at.is_not(None),
                        QuarantineItem.resolved_at < cutoff,
                    )
                    .limit(1000)
                )
            )
            if not ids:
                break
            res = session.execute(delete(QuarantineItem).where(QuarantineItem.id.in_(ids)))
            total += res.rowcount or len(ids)
            session.commit()
        return total

    try:
        audit_deleted = _batched_audit()
        quarantine_deleted = _batched_quarantine()
    except OperationalError:
        # SQLite busy (database is locked) even after busy_timeout; retry
        # once so the daily cleanup doesn't wedged. No sleep here — caller is
        # async (cleanup_loop) and blocking the event loop would stall all
        # concurrent requests.
        session.rollback()
        log.warning("retention cleanup busy, retrying once")
        try:
            audit_deleted = _batched_audit()
            quarantine_deleted = _batched_quarantine()
        except OperationalError:
            session.rollback()
            log.warning("retention cleanup still busy, skipping this cycle")
            return {"audit": 0, "quarantine": 0}
    if audit_deleted or quarantine_deleted:
        log.info(
            "retention cleanup: deleted %d audit rows, %d quarantine rows (retention=%dd)",
            audit_deleted,
            quarantine_deleted,
            retention_days,
        )
    return {"audit": audit_deleted, "quarantine": quarantine_deleted}


async def cleanup_loop(session_factory, retention_days: int, stop: asyncio.Event) -> None:
    """Run cleanup once, then every 24h until ``stop`` is set."""
    while not stop.is_set():
        try:
            with session_factory() as session:
                cleanup_expired(session, retention_days)
        except Exception:  # noqa: BLE001 — keep the loop alive
            log.exception("retention cleanup failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=_CLEANUP_INTERVAL)
        except TimeoutError:
            continue
