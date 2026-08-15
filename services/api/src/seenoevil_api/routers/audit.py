"""Audit log query and maintenance endpoints."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import audit_sig
from ..models import AuditDecision
from ..schemas import AuditOut


def make_router(get_session_dep, require_user) -> APIRouter:
    r = APIRouter(prefix="/v1/audit", tags=["audit"])

    @r.get("", response_model=list[AuditOut], dependencies=[Depends(require_user)])
    def list_audit(
        session: Session = Depends(get_session_dep),
        device_id: int | None = Query(default=None),
        decision: str | None = Query(default=None, pattern="^(allow|block)$"),
        since: datetime | None = Query(default=None),
        # Cursor-based pagination: pass the smallest id from the previous page
        # to fetch the next chunk. ``limit`` is the page size.
        before_id: int | None = Query(default=None, ge=1),
        limit: int = Query(default=100, ge=1, le=1000),
    ) -> list[AuditOut]:
        stmt = select(AuditDecision).order_by(AuditDecision.id.desc())
        if device_id is not None:
            stmt = stmt.where(AuditDecision.device_id == device_id)
        if decision is not None:
            stmt = stmt.where(AuditDecision.decision == decision)
        if since is not None:
            stmt = stmt.where(AuditDecision.ts >= since)
        if before_id is not None:
            stmt = stmt.where(AuditDecision.id < before_id)
        rows = list(session.scalars(stmt.limit(limit)))
        if not rows:
            return []
        secret = audit_sig.ensure_secret(session)
        return [
            AuditOut.model_validate(row).model_copy(
                update={
                    "signature_valid": (
                        None
                        if row.signature is None  # legacy pre-signature row
                        else audit_sig.verify_row(secret, row)
                    )
                }
            )
            for row in rows
        ]

    @r.delete(
        "",
        status_code=status.HTTP_204_NO_CONTENT,
        response_class=Response,
    )
    def clear_audit(
        session: Session = Depends(get_session_dep),
        current: tuple[str, str] = Depends(require_user),
    ) -> Response:
        _, role = current
        if role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "admin role required")
        session.execute(delete(AuditDecision))
        session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return r
