"""Quota usage tracking.

The proxy (or any future agent on the device) periodically POSTs minutes of
active use to ``/v1/quota/heartbeat``. The handler upserts today's row for
that device. The decide endpoint reads the same rows when evaluating
``profile.quota_minutes_per_day``.

Active-use is intentionally vague: see-no-evil counts wall-clock minutes
during which the proxy observed traffic from the device; how those minutes
are sampled is up to the caller.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ..auth import require_proxy_factory
from ..models import Device, Quota
from ..schemas import QuotaHeartbeat, QuotaStatus


def _resolve_device(session: Session, body: QuotaHeartbeat) -> Device | None:
    if body.device_id is not None:
        return session.get(Device, body.device_id)
    if body.client_ip:
        return session.scalars(select(Device).where(Device.ip == body.client_ip)).first()
    if body.device_mac:
        return session.scalars(select(Device).where(Device.mac == body.device_mac)).first()
    return None


def make_router(get_session_dep, require_admin, get_config) -> APIRouter:
    r = APIRouter(prefix="/v1/quota", tags=["quota"])
    require_proxy = require_proxy_factory(get_config)

    @r.post("/heartbeat", response_model=QuotaStatus)
    def heartbeat(
        body: QuotaHeartbeat,
        session: Session = Depends(get_session_dep),
        _proxy: str = Depends(require_proxy),
    ) -> QuotaStatus:
        device = _resolve_device(session, body)
        if device is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")

        today = datetime.now(UTC).date()
        minutes = int(body.minutes)
        # Atomic upsert avoids the SELECT-then-INSERT/UPDATE race where two
        # concurrent heartbeats both see no row and one hits a unique-violation,
        # or both read the same counter and one increment is lost (#15).
        try:
            bind = session.get_bind()
            dialect = bind.dialect.name if bind is not None else "sqlite"
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as sqlite_insert

                stmt = sqlite_insert(Quota).values(
                    device_id=device.id, day=today, minutes_used=minutes
                )
                stmt = stmt.on_conflict_do_update(
                    index_elements=["device_id", "day"],
                    set_={"minutes_used": Quota.minutes_used + stmt.excluded.minutes_used},
                )
            else:
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(Quota).values(device_id=device.id, day=today, minutes_used=minutes)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["device_id", "day"],
                    set_={"minutes_used": Quota.minutes_used + stmt.excluded.minutes_used},
                )
            session.execute(stmt)
            session.commit()
        except OperationalError as err:
            # SQLite will raise OperationalError (database is locked / busy)
            # even with busy_timeout if two writers overlap beyond 5s. Roll
            # back and return a retryable 503 so the proxy retries next tick.
            session.rollback()
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "quota busy, retry") from err
        row = session.scalars(
            select(Quota).where(Quota.device_id == device.id, Quota.day == today)
        ).first()
        if row is None:  # pragma: no cover - should not happen after upsert
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "quota busy, retry")
        return QuotaStatus(
            device_id=device.id,
            day=today.isoformat(),
            minutes_used=int(row.minutes_used),
            minutes_quota=int(device.profile.quota_minutes_per_day or 0),
        )

    @r.get("/{device_id}", response_model=QuotaStatus, dependencies=[Depends(require_admin)])
    def get_quota(
        device_id: int,
        day: date | None = None,
        session: Session = Depends(get_session_dep),
    ) -> QuotaStatus:
        device = session.get(Device, device_id)
        if device is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
        d = day or datetime.now(UTC).date()
        row = session.scalars(
            select(Quota).where(Quota.device_id == device.id, Quota.day == d)
        ).first()
        return QuotaStatus(
            device_id=device.id,
            day=d.isoformat(),
            minutes_used=int(row.minutes_used) if row else 0,
            minutes_quota=int(device.profile.quota_minutes_per_day or 0),
        )

    @r.delete(
        "/{device_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        dependencies=[Depends(require_admin)],
    )
    def reset_quota(
        device_id: int,
        day: date | None = None,
        session: Session = Depends(get_session_dep),
    ) -> None:
        device = session.get(Device, device_id)
        if device is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "device not found")
        d = day or datetime.now(UTC).date()
        row = session.scalars(
            select(Quota).where(Quota.device_id == device.id, Quota.day == d)
        ).first()
        if row is not None:
            row.minutes_used = 0
            session.commit()

    return r
