"""Admin panic-relax endpoint.

Three operations:

* ``GET    /v1/admin/panic`` — current state (anyone authenticated).
* ``POST   /v1/admin/panic`` — enable for ``duration_minutes`` (admin).
* ``DELETE /v1/admin/panic`` — disable now (admin).

Every state change writes an ``AuditDecision`` row with a synthetic URL so the
existing audit log surface picks it up without a separate table.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy.orm import Session

from .. import notifications, panic
from ..config import AppConfig
from ..models import AuditDecision
from ..schemas import PanicSet, PanicStatus


def _to_status(state: panic.PanicState) -> PanicStatus:
    return PanicStatus(
        active=state.active,
        until=state.until,
        reason=state.reason,
        set_by=state.set_by,
        set_at=state.set_at,
    )


def _audit(session: Session, *, action: str, who: str | None, reason: str) -> None:
    session.add(
        AuditDecision(
            ts=datetime.now(UTC).replace(tzinfo=None),
            device_id=None,
            profile_id=None,
            url=f"seenoevil://panic/{action}",
            content_type=None,
            decision="allow" if action == "enable" else "block",
            reason=f"panic_{action}:{reason or ''}"[:128],
            classifier_scores={"set_by": who or ""},
        )
    )


def make_router(get_session_dep, require_admin, get_config) -> APIRouter:
    r = APIRouter(prefix="/v1/admin/panic", tags=["panic"])

    @r.get("", response_model=PanicStatus)
    def get_panic(session: Session = Depends(get_session_dep)) -> PanicStatus:
        return _to_status(panic.get_state(session))

    @r.post("", response_model=PanicStatus)
    def set_panic(
        body: PanicSet,
        background: BackgroundTasks,
        session: Session = Depends(get_session_dep),
        admin_email: str = Depends(require_admin),
        config: AppConfig = Depends(get_config),
    ) -> PanicStatus:
        who = admin_email or "admin"
        state = panic.set_active(
            session,
            duration_minutes=body.duration_minutes,
            reason=body.reason,
            set_by=who,
        )
        _audit(session, action="enable", who=who, reason=body.reason)
        session.commit()
        background.add_task(
            notifications.send_panic_change,
            config.notifications,
            active=True,
            set_by=who,
            reason=body.reason,
            until=state.until,
        )
        return _to_status(state)

    @r.delete("", response_model=PanicStatus)
    def clear_panic(
        background: BackgroundTasks,
        session: Session = Depends(get_session_dep),
        admin_email: str = Depends(require_admin),
        config: AppConfig = Depends(get_config),
    ) -> PanicStatus:
        who = admin_email or "admin"
        panic.clear(session)
        _audit(session, action="disable", who=who, reason="")
        session.commit()
        background.add_task(
            notifications.send_panic_change,
            config.notifications,
            active=False,
            set_by=who,
            reason="",
            until=None,
        )
        return _to_status(panic.get_state(session))

    return r
