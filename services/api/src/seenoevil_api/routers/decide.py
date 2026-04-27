"""Policy decision endpoint, called by the proxy."""

from __future__ import annotations

from datetime import UTC, date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import notifications, panic
from ..config import AppConfig
from ..models import AuditDecision, Device, Profile, QuarantineItem, Quota
from ..policy import DecisionInput, ProfileView, decide, now_parts
from ..schemas import DecideRequest, DecideResponse


def _profile_view(p: Profile) -> ProfileView:
    return ProfileView(
        name=p.name,
        image_thresholds=dict(p.image_thresholds or {}),
        schedule=dict(p.schedule or {}),
        quota_minutes_per_day=int(p.quota_minutes_per_day or 0),
        allow_domains=list(p.allow_domains or []),
        deny_domains=list(p.deny_domains or []),
        deny_url_keywords=list(p.deny_url_keywords or []),
        allow_youtube_channels=list(p.allow_youtube_channels or []),
        deny_youtube_channels=list(p.deny_youtube_channels or []),
    )


def _resolve_device(session: Session, body: DecideRequest) -> tuple[Device | None, Profile | None]:
    device: Device | None = None
    if body.device_id is not None:
        device = session.get(Device, body.device_id)
    elif body.device_mac:
        device = session.scalars(select(Device).where(Device.mac == body.device_mac)).first()
    profile = device.profile if device else None
    return device, profile


def _default_profile(session: Session, name: str) -> Profile | None:
    return session.scalars(select(Profile).where(Profile.name == name)).first()


def _minutes_used_today(session: Session, device: Device | None, today: date) -> int:
    if device is None:
        return 0
    row = session.scalars(
        select(Quota).where(Quota.device_id == device.id, Quota.day == today)
    ).first()
    return int(row.minutes_used) if row else 0


def _should_quarantine(reason: str, content_type: str | None) -> bool:
    """Hold for review when a classifier blocked an image/video response.

    Schedule/quota/domain blocks are too high-volume and not interesting to
    review one-by-one.
    """
    if not reason.startswith("classifier:"):
        return False
    if content_type is None:
        return True
    ct = content_type.lower()
    return ct.startswith("image/") or ct.startswith("video/")


def make_router(get_session_dep, get_config) -> APIRouter:
    r = APIRouter(prefix="/v1", tags=["decide"])

    @r.post("/decide", response_model=DecideResponse)
    def post_decide(
        body: DecideRequest,
        background: BackgroundTasks,
        session: Session = Depends(get_session_dep),
        config: AppConfig = Depends(get_config),
    ) -> DecideResponse:
        device, profile = _resolve_device(session, body)
        if profile is None:
            profile = _default_profile(session, config.devices.default_profile)
        if profile is None:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "no profile available (configure profiles first)",
            )

        now = datetime.now(UTC)
        dow, t, today = now_parts(now)
        panic_state = panic.get_state(session)
        decision = decide(
            _profile_view(profile),
            DecisionInput(
                url=body.url,
                content_type=body.content_type,
                classifier_scores=dict(body.classifier_scores),
                now_dow=dow,
                now_time=t,
                today=today,
                minutes_used_today=_minutes_used_today(session, device, today),
                panic_relax=panic_state.active,
            ),
            config=config,
        )

        session.add(
            AuditDecision(
                device_id=device.id if device else None,
                profile_id=profile.id,
                url=body.url,
                content_type=body.content_type,
                decision=decision.decision,
                reason=decision.reason,
                classifier_scores=dict(body.classifier_scores),
            )
        )

        if decision.decision == "block" and _should_quarantine(decision.reason, body.content_type):
            session.add(
                QuarantineItem(
                    device_id=device.id if device else None,
                    profile_id=profile.id,
                    url=body.url,
                    content_type=body.content_type,
                    reason=decision.reason,
                    classifier_scores=dict(body.classifier_scores),
                    thumbnail_b64=body.thumbnail_b64,
                    status="pending",
                )
            )

        session.commit()

        if (
            decision.decision == "block"
            and bool(getattr(profile, "notify_on_block", False))
            and decision.reason != "panic_relax"
        ):
            background.add_task(
                notifications.send_block,
                config.notifications,
                profile=profile.name,
                device_mac=device.mac if device else body.device_mac,
                url=body.url,
                reason=decision.reason,
                classifier_scores=dict(body.classifier_scores),
            )

        return DecideResponse(
            decision=decision.decision,
            reason=decision.reason,
            profile=profile.name,
            device_id=device.id if device else None,
        )

    return r
