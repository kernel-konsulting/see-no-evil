"""Quarantine queue: list pending blocks and let an admin allow/deny them.

All endpoints are admin-only because quarantined previews may contain sensitive
blocked content.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import QuarantineItem
from ..schemas import QuarantineOut

_VALID_STATUSES = {"pending", "allowed", "denied"}
_PENDING_TTL = timedelta(hours=1)


def _pending_cutoff() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None) - _PENDING_TTL


def _naive_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts
    return ts.astimezone(UTC).replace(tzinfo=None)


class FlagRequest(BaseModel):
    note: str = Field(default="", max_length=500)


class BulkResolveRequest(BaseModel):
    ids: list[int] | None = Field(default=None, max_length=1000)


class BulkResolveResponse(BaseModel):
    updated: int


def make_router(get_session_dep, require_admin, require_user, current_user) -> APIRouter:
    r = APIRouter(prefix="/v1/quarantine", tags=["quarantine"])

    @r.get("", response_model=list[QuarantineOut], dependencies=[Depends(require_user)])
    def list_quarantine(
        session: Session = Depends(get_session_dep),
        status_: str = Query(default="pending", alias="status"),
        limit: int = Query(default=100, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        _current: tuple[str, str] = Depends(current_user),
    ) -> list[QuarantineItem]:
        if status_ not in _VALID_STATUSES and status_ != "all":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid status")
        stmt = select(QuarantineItem).order_by(QuarantineItem.ts.desc()).offset(offset).limit(limit)
        if status_ != "all":
            stmt = stmt.where(QuarantineItem.status == status_)
        if status_ == "pending":
            stmt = stmt.where(QuarantineItem.ts >= _pending_cutoff())
        return list(session.scalars(stmt))

    @r.get("/{item_id}", response_model=QuarantineOut, dependencies=[Depends(require_user)])
    def get_quarantine(
        item_id: int,
        session: Session = Depends(get_session_dep),
        _current: tuple[str, str] = Depends(current_user),
    ) -> QuarantineItem:
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
        if _naive_utc(obj.ts) < _pending_cutoff():
            raise HTTPException(status.HTTP_409_CONFLICT, "quarantine item expired")
        obj.status = new_status
        obj.resolved_at = datetime.now(UTC)
        obj.resolved_by = who
        session.commit()
        session.refresh(obj)
        return obj

    def _bulk_resolve(
        body: BulkResolveRequest,
        new_status: str,
        session: Session,
        who: str,
    ) -> BulkResolveResponse:
        stmt = select(QuarantineItem).where(QuarantineItem.status == "pending")
        if body.ids is not None:
            if not body.ids:
                return BulkResolveResponse(updated=0)
            stmt = stmt.where(QuarantineItem.id.in_(body.ids))
        stmt = stmt.where(QuarantineItem.ts >= _pending_cutoff())
        items = list(session.scalars(stmt))
        now = datetime.now(UTC)
        for obj in items:
            obj.status = new_status
            obj.resolved_at = now
            obj.resolved_by = who
        session.commit()
        return BulkResolveResponse(updated=len(items))

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

    @r.post(
        "/bulk-allow",
        response_model=BulkResolveResponse,
        dependencies=[Depends(require_admin)],
    )
    def allow_bulk(
        body: BulkResolveRequest | None = None,
        session: Session = Depends(get_session_dep),
    ) -> BulkResolveResponse:
        return _bulk_resolve(body or BulkResolveRequest(), "allowed", session, who="admin")

    @r.post(
        "/bulk-deny",
        response_model=BulkResolveResponse,
        dependencies=[Depends(require_admin)],
    )
    def deny_bulk(
        body: BulkResolveRequest | None = None,
        session: Session = Depends(get_session_dep),
    ) -> BulkResolveResponse:
        return _bulk_resolve(body or BulkResolveRequest(), "denied", session, who="admin")

    @r.post(
        "/{item_id}/flag",
        response_model=QuarantineOut,
        dependencies=[Depends(require_user)],
    )
    def flag_item(
        item_id: int,
        body: FlagRequest,
        session: Session = Depends(get_session_dep),
        current: tuple[str, str] = Depends(current_user),
    ) -> QuarantineItem:
        """Mark an item as a suspected false positive.

        Does not change the lifecycle status; it records who flagged it and an
        optional note so another admin can review it.
        """
        obj = session.get(QuarantineItem, item_id)
        if obj is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "quarantine item not found")
        email, _role = current
        obj.flag_note = (body.note or "").strip() or "(no note)"
        obj.flagged_by = email
        obj.flagged_at = datetime.now(UTC)
        session.commit()
        session.refresh(obj)
        return obj

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
