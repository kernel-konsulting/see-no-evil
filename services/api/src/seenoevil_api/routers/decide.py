"""Policy decision endpoint, called by the proxy."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import notifications, panic, runtime
from ..config import AppConfig
from ..models import AuditDecision, Device, Profile, QuarantineItem, Quota
from ..policy import DecisionInput, DecisionOutput, GlobalRules, ProfileView, decide, now_parts
from ..schemas import DecideRequest, DecideResponse

log = logging.getLogger("seenoevil_api.decide")


def _profile_view(p: Profile) -> ProfileView:
    return ProfileView(
        name=p.name,
        image_thresholds=dict(p.image_thresholds or {}),
        schedule=dict(p.schedule or {}),
        quota_minutes_per_day=int(p.quota_minutes_per_day or 0),
        allow_domains=list(p.allow_domains or []),
        enforce_allowlist=bool(p.enforce_allowlist or False),
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
    if device is None and body.client_ip:
        device = session.scalars(select(Device).where(Device.ip == body.client_ip)).first()
    profile = device.profile if device else None
    return device, profile


def _default_profile(session: Session, name: str) -> Profile | None:
    return session.scalars(select(Profile).where(Profile.name == name)).first()


def _synthetic_mac_from_ip(ip: str) -> str | None:
    """Build a deterministic locally-administered MAC from an IPv4 address.

    Used to seed an auto-discovered ``Device`` row when the proxy can only
    report the source IP (no real MAC available, e.g. rootless container
    networking). The scanner can later overwrite this with the real MAC.
    """
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return None
    if any(o < 0 or o > 255 for o in octets):
        return None
    # 02:00 = locally-administered, unicast.
    return f"02:00:{octets[0]:02x}:{octets[1]:02x}:{octets[2]:02x}:{octets[3]:02x}"


def _auto_create_device(
    session: Session,
    *,
    mac: str | None,
    ip: str | None,
    profile: Profile,
) -> Device | None:
    """Create a new Device row for a previously-unseen MAC or IP.

    Called when the proxy posts a decision for a client the API has never
    seen before, so admins can discover devices passively (in addition to
    the scanner sweep and manual creation).
    """
    seed_mac = mac or (_synthetic_mac_from_ip(ip) if ip else None)
    if not seed_mac:
        return None
    if mac:
        suffix = mac.replace(":", "")[-6:].upper()
        name = f"auto-{suffix}"
    elif ip:
        name = f"auto-{ip}"
    else:
        name = "auto-device"
    device = Device(
        mac=seed_mac,
        name=name,
        profile_id=profile.id,
        ip=ip,
        last_seen_at=datetime.now(UTC),
    )
    session.add(device)
    try:
        session.flush()
    except Exception:  # pragma: no cover - race: another writer just inserted it
        session.rollback()
        return session.scalars(select(Device).where(Device.mac == seed_mac)).first()
    return device


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


def _global_rules(session: Session) -> GlobalRules:
    settings = runtime.get_runtime(session)
    inspect = settings.get("inspect", {}) if isinstance(settings, dict) else {}
    lists = settings.get("lists", {}) if isinstance(settings, dict) else {}
    image = settings.get("image", {}) if isinstance(settings, dict) else {}
    image_thresholds: dict[str, float] = {}
    for label, key in (
        ("sexy", "sexy_threshold"),
        ("porn", "porn_threshold"),
        ("hentai", "hentai_threshold"),
    ):
        value = image.get(key)
        if isinstance(value, int | float) and value > 0:
            image_thresholds[label] = float(value)
    return GlobalRules(
        allow_domains=list(lists.get("global_allow_domains") or []),
        enforce_allowlist=bool(lists.get("enforce_global_allowlist") or False),
        deny_domains=list(lists.get("global_deny_domains") or []),
        deny_url_keywords=list(lists.get("global_deny_keywords") or []),
        apply_domain_rules=bool(inspect.get("domain", True)),
        apply_url_rules=bool(inspect.get("url", True)),
        image_thresholds=image_thresholds,
    )


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

        # Auto-discover devices on first request: if the proxy reported a MAC
        # or IP we've never seen, create a Device row attached to the default
        # profile. Admins can rename / re-profile from the UI.
        if device is None and (body.device_mac or body.client_ip):
            device = _auto_create_device(
                session, mac=body.device_mac, ip=body.client_ip, profile=profile
            )
        elif device is not None:
            device.last_seen_at = datetime.now(UTC)
            if body.client_ip and device.ip != body.client_ip:
                device.ip = body.client_ip

        now = datetime.now(UTC)
        dow, t, today = now_parts(now)
        panic_state = panic.get_state(session)
        if panic_state.active:
            decision = DecisionOutput("allow", "panic_relax")
        elif body.decision in {"allow", "block"}:
            decision = DecisionOutput(body.decision, body.reason or "proxy")
        else:
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
                    panic_relax=False,
                ),
                config=config,
                global_rules=_global_rules(session),
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
                thumbnail_b64=body.thumbnail_b64,
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

        log.info(
            "decide %s reason=%s profile=%s device_id=%s mac=%s ip=%s url=%s",
            decision.decision,
            decision.reason,
            profile.name,
            device.id if device else None,
            device.mac if device else body.device_mac,
            body.client_ip,
            body.url,
        )

        return DecideResponse(
            decision=decision.decision,
            reason=decision.reason,
            profile=profile.name,
            device_id=device.id if device else None,
        )

    return r
