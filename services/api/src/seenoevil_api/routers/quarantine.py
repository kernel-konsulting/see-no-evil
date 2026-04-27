"""Quarantine queue: list pending blocks and let an admin allow/deny them."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import QuarantineItem
from ..schemas import QuarantineOut

_VALID_STATUSES = {"pending", "allowed", "denied"}


def make_router(get_session_dep, require_admin) -> APIRouter:
    r = APIRouter(prefix="/v1/quarantine", tags=["quarantine"])

    @r.get("", response_model=list[QuarantineOut], dependencies=[Depends(require_admin)])
    def list_quarantine(
        session: Session = Depends(get_session_dep),
        status_: str = Query(default="pending", alias="status"),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[QuarantineItem]:
        if status_ not in _VALID_STATUSES and status_ != "all":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid status")
        stmt = select(QuarantineItem).order_by(QuarantineItem.ts.desc()).limit(limit)
        if status_ != "all":
            stmt = stmt.where(QuarantineItem.status == status_)
        return list(session.scalars(stmt))

    @r.get("/{item_id}", response_model=QuarantineOut, dependencies=[Depends(require_admin)])
    def get_quarantine(item_id: int, session: Session = Depends(get_session_dep)) -> QuarantineItem:
        obj = session.get(QuarantineItem, item_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "quarantine item not found")
        return obj

    def _resolve(item_id: int, new_status: str, session: Session, who: str) -> QuarantineItem:
        obj = session.get(QuarantineItem, item_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "quarantine item not found")
        if obj.status != "pending":
            raise HTTPException(status.HTTP_409_CONFLICT, f"already {obj.status}")
        obj.status = new_status
        obj.resolved_at = datetime.now(UTC)
        obj.resolved_by = who
        session.commit()
        session.refresh(obj)
        return obj

    @r.post(
        "/{item_id}/allow",
        response_model=QuarantineOut,
        dependencies=[Depends(require_admin)],
    )
    def allow_item(item_id: int, session: Session = Depends(get_session_dep)) -> QuarantineItem:
        return _resolve(item_id, "allowed", session, who="admin")

    @r.post(
        "/{item_id}/deny",
        response_model=QuarantineOut,
        dependencies=[Depends(require_admin)],
    )
    def deny_item(item_id: int, session: Session = Depends(get_session_dep)) -> QuarantineItem:
        return _resolve(item_id, "denied", session, who="admin")

    @r.delete(
        "/{item_id}",
        status_code=status.HTTP_204_NO_CONTENT,
        response_model=None,
        dependencies=[Depends(require_admin)],
    )
    def delete_item(item_id: int, session: Session = Depends(get_session_dep)) -> None:
        obj = session.get(QuarantineItem, item_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "quarantine item not found")
        session.delete(obj)
        session.commit()

    return r
