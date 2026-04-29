"""Dashboard aggregation endpoint.

Returns allow / block / quarantine counts bucketed by time windows.
Read-access is open to any authenticated user (admin or viewer).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import AuditDecision, Device, QuarantineItem


class WindowStats(BaseModel):
    label: str
    seconds: int | None  # ``None`` means "all time"
    allowed: int
    blocked: int
    quarantined_pending: int


class DashboardStats(BaseModel):
    devices: int
    quarantine_pending: int
    windows: list[WindowStats]


_WINDOWS: list[tuple[str, int | None]] = [
    ("Last hour", 3600),
    ("Last 24 hours", 86_400),
    ("Last 7 days", 7 * 86_400),
    ("Last 30 days", 30 * 86_400),
    ("All time", None),
]


def make_router(get_session_dep, require_user) -> APIRouter:
    r = APIRouter(prefix="/v1/dashboard", tags=["dashboard"])

    @r.get("/stats", response_model=DashboardStats, dependencies=[Depends(require_user)])
    def stats(session: Session = Depends(get_session_dep)) -> DashboardStats:
        now = datetime.now(UTC)
        device_count = session.scalar(select(func.count()).select_from(Device)) or 0
        quarantine_pending = (
            session.scalar(
                select(func.count())
                .select_from(QuarantineItem)
                .where(QuarantineItem.status == "pending")
            )
            or 0
        )

        windows: list[WindowStats] = []
        for label, seconds in _WINDOWS:
            stmt = select(AuditDecision.decision, func.count()).group_by(AuditDecision.decision)
            qstmt = (
                select(func.count())
                .select_from(QuarantineItem)
                .where(QuarantineItem.status == "pending")
            )
            if seconds is not None:
                since = now - timedelta(seconds=seconds)
                stmt = stmt.where(AuditDecision.ts >= since)
                qstmt = qstmt.where(QuarantineItem.ts >= since)
            counts = {row[0]: int(row[1]) for row in session.execute(stmt).all()}
            quar = int(session.scalar(qstmt) or 0)
            windows.append(
                WindowStats(
                    label=label,
                    seconds=seconds,
                    allowed=counts.get("allow", 0),
                    blocked=counts.get("block", 0),
                    quarantined_pending=quar,
                )
            )

        return DashboardStats(
            devices=int(device_count),
            quarantine_pending=int(quarantine_pending),
            windows=windows,
        )

    return r
