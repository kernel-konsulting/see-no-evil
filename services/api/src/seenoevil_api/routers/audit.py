"""Read-only audit log query."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditDecision
from ..schemas import AuditOut


def make_router(get_session_dep, require_admin) -> APIRouter:
    r = APIRouter(prefix="/v1/audit", tags=["audit"])

    @r.get("", response_model=list[AuditOut], dependencies=[Depends(require_admin)])
    def list_audit(
        session: Session = Depends(get_session_dep),
        device_id: int | None = Query(default=None),
        decision: str | None = Query(default=None, pattern="^(allow|block)$"),
        since: datetime | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[AuditDecision]:
        stmt = select(AuditDecision).order_by(AuditDecision.ts.desc()).limit(limit)
        if device_id is not None:
            stmt = stmt.where(AuditDecision.device_id == device_id)
        if decision is not None:
            stmt = stmt.where(AuditDecision.decision == decision)
        if since is not None:
            stmt = stmt.where(AuditDecision.ts >= since)
        return list(session.scalars(stmt))

    return r
