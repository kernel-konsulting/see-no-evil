"""Policy decision endpoint, called by the proxy."""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload

from .. import audit_sig, notifications, panic, runtime
from ..auth import require_proxy_factory
from ..config import AppConfig
from ..models import AuditDecision, Device, Profile, QuarantineItem, Quota
from ..policy import DecisionInput, DecisionOutput, GlobalRules, ProfileView, decide, now_parts
from ..policy_opa import build_opa_input, opa_decide
from ..quota_day import quota_day as _quota_today
from ..schemas import DecideRequest, DecideResponse

try:
    from prometheus_client import Counter

    _decide_total = Counter(
        "seenoevil_decide_requests_total",
        "Decide requests by engine and decision",
        ["engine", "decision"],
    )
    _opa_fallback_total = Counter(
        "seenoevil_opa_fallback_total",
        "OPA fallback to Python (auto mode)",
    )
except Exception:  # pragma: no cover - metrics optional in tests
    _decide_total = None  # type: ignore[assignment]
    _opa_fallback_total = None  # type: ignore[assignment]

log = logging.getLogger("seenoevil_api.decide")

# Max base64 thumbnail size stored in audit/quarantine. Matches the
# Pydantic max_length on DecideRequest.thumbnail_b64 (#41, #12).
MAX_THUMBNAIL_B64 = 50000


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


def quota_day(config: AppConfig) -> date:
    """Public alias for _quota_today (used by quota router)."""

    return _quota_today(config)


def _resolve_device(session: Session, body: DecideRequest) -> tuple[Device | None, Profile | None]:
    device: Device | None = None
    if body.device_id is not None:
        # Use joinedload to fetch profile eagerly and avoid extra round-trip
        # under SQLite busy conditions (F12).
        device = session.scalars(
            select(Device).where(Device.id == body.device_id).options(joinedload(Device.profile))
        ).first()
        # Fallback to session.get if not found via select (unlikely)
        if device is None:
            device = session.get(Device, body.device_id)
    elif body.client_ip:
        # Preferred path: the proxy attributes by source IP (client MACs are
        # invisible at the TCP layer, and client-supplied MACs are untrusted).
        device = session.scalars(
            select(Device).where(Device.ip == body.client_ip).options(joinedload(Device.profile))
        ).first()
    elif body.device_mac:
        # Kept for compatibility; the proxy no longer sends client-supplied MACs.
        device = session.scalars(
            select(Device).where(Device.mac == body.device_mac).options(joinedload(Device.profile))
        ).first()
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
    # F06: if IP already exists, return existing to avoid synthetic MAC collision
    if ip:
        existing_by_ip = session.scalars(select(Device).where(Device.ip == ip)).first()
        if existing_by_ip is not None:
            # Refresh last_seen so device attribution stays fresh
            existing_by_ip.last_seen_at = datetime.now(UTC)
            if ip and existing_by_ip.ip != ip:
                existing_by_ip.ip = ip
            try:
                session.flush()
            except Exception:
                session.rollback()
            return existing_by_ip
    seed_mac = mac or (_synthetic_mac_from_ip(ip) if ip else None)
    if not seed_mac:
        return None
    # Avoid synthetic MAC collision: if seed_mac already exists, return it
    existing_by_mac = session.scalars(select(Device).where(Device.mac == seed_mac)).first()
    if existing_by_mac is not None:
        existing_by_mac.last_seen_at = datetime.now(UTC)
        if ip and existing_by_mac.ip != ip:
            existing_by_mac.ip = ip
        try:
            session.flush()
        except Exception:
            session.rollback()
        return existing_by_mac
    # F06: rate limiting — don't auto-create if too many devices created recently
    try:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
        recent_count = session.scalar(
            select(func.count()).select_from(Device).where(Device.created_at >= cutoff)
        )
        if recent_count is not None and recent_count > 20:
            log.warning("auto-create rate limited: %s devices in last hour", recent_count)
            return None
    except Exception:
        # If count query fails (e.g. SQLite busy), fall through and attempt create
        pass
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
        # On race, re-check both IP and MAC
        if ip:
            by_ip = session.scalars(select(Device).where(Device.ip == ip)).first()
            if by_ip is not None:
                return by_ip
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
    require_proxy = require_proxy_factory(get_config)

    @r.post("/decide", response_model=DecideResponse)
    def post_decide(
        body: DecideRequest,
        background: BackgroundTasks,
        session: Session = Depends(get_session_dep),
        config: AppConfig = Depends(get_config),
        _proxy: str = Depends(require_proxy),
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

        # Use pod-local timezone for schedule checks; UTC would shift windows
        # for non-UTC deployments (#29).
        try:
            tz = ZoneInfo(config.pod.timezone or "UTC")
        except (ZoneInfoNotFoundError, ValueError):
            log.warning("invalid pod timezone %r, falling back to UTC", config.pod.timezone)
            tz = ZoneInfo("UTC")
        now = datetime.now(tz)
        dow, t, today = now_parts(now)
        panic_state = panic.get_state(session)
        if panic_state.active:
            decision = DecisionOutput("allow", "panic_relax")
        elif body.decision == "block" and body.reason and body.reason.startswith("classifier:"):
            # Fail-closed audit path: proxy pre-blocked due to classifier/policy failure
            # with empty scores (e.g. classifier:unavailable, video_sampler:no_frames).
            # Trust block for audit/quarantine, but still validate reason is classifier:*.
            # This closes allow-bypass (F03) while preserving forensics for fail-closed blocks.
            # Normalize classifier:image:porn -> classifier:porn to match policy engine.
            norm = body.reason.strip()
            for prefix in ("classifier:image:", "classifier:video:", "classifier:text:"):
                if norm.startswith(prefix):
                    norm = "classifier:" + norm[len(prefix) :]
                    break
            decision = DecisionOutput("block", norm)
        else:
            # Policy engine selection (M2.1): python | opa | auto
            engine = getattr(getattr(config, "policy", None), "engine", "python")
            if engine not in ("python", "opa", "auto"):
                engine = "python"
            decision = None  # type: ignore[assignment]
            opa_error: Exception | None = None
            if engine in ("opa", "auto"):
                try:
                    pv = _profile_view(profile)
                    din = DecisionInput(
                        url=body.url,
                        content_type=body.content_type,
                        classifier_scores=dict(body.classifier_scores),
                        now_dow=dow,
                        now_time=t,
                        today=today,
                        minutes_used_today=_minutes_used_today(session, device, today),
                        panic_relax=False,
                    )
                    gr = _global_rules(session)
                    opa_input = build_opa_input(pv, din, config=config, global_rules=gr)
                    opa_url = getattr(config.policy, "opa_url", "http://opa:8181")
                    opa_timeout = int(getattr(config.policy, "opa_timeout_ms", 1500))
                    decision = opa_decide(opa_input, opa_url=opa_url, timeout_ms=opa_timeout)
                    if _decide_total:
                        _decide_total.labels(engine="opa", decision=decision.decision).inc()
                except Exception as exc:  # pragma: no cover - fallback path
                    opa_error = exc
                    log.warning("opa decide failed (engine=%s): %s", engine, exc)
                    if engine == "opa":
                        raise HTTPException(
                            status.HTTP_503_SERVICE_UNAVAILABLE,
                            f"policy engine opa unavailable: {exc}",
                        ) from exc
                    if _opa_fallback_total:
                        _opa_fallback_total.inc()
                    # auto: fall through to python
                    decision = None
            if decision is None:
                if opa_error is not None:
                    log.info("opa fallback to python (auto mode)")
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
                if _decide_total and engine in ("python", "auto"):
                    label = "python" if engine == "python" else "fallback"
                    _decide_total.labels(engine=label, decision=decision.decision).inc()

        # Bound thumbnail size before persisting to avoid unbounded Text growth
        # (#12, #41). Oversize previews are dropped rather than rejecting the
        # whole decision — the proxy always has a valid verdict.
        thumb = body.thumbnail_b64
        if thumb is not None and len(thumb) > MAX_THUMBNAIL_B64:
            log.warning("dropping oversize thumbnail_b64", extra={"size": len(thumb)})
            thumb = None

        audit_row = AuditDecision(
            device_id=device.id if device else None,
            profile_id=profile.id,
            url=body.url,
            content_type=body.content_type,
            decision=decision.decision,
            reason=decision.reason,
            classifier_scores=dict(body.classifier_scores),
            thumbnail_b64=thumb,
        )
        session.add(audit_row)
        # Flush so id/ts are populated, then sign the row (tamper detection).
        session.flush()
        audit_sig.sign_row(session, audit_row)

        if decision.decision == "block" and _should_quarantine(decision.reason, body.content_type):
            session.add(
                QuarantineItem(
                    device_id=device.id if device else None,
                    profile_id=profile.id,
                    url=body.url,
                    content_type=body.content_type,
                    reason=decision.reason,
                    classifier_scores=dict(body.classifier_scores),
                    thumbnail_b64=thumb,
                    status="pending",
                )
            )

        try:
            session.commit()
        except OperationalError:
            session.rollback()
            log.warning("decide commit busy, returning decision without audit persistence")
            # Still return the policy decision — the audit/quarantine row will be
            # missing but the user experience (allow/block) must not fail due to
            # a transient SQLite busy.
            return DecideResponse(
                decision=decision.decision,
                reason=decision.reason,
                profile=profile.name,
                device_id=device.id if device else None,
            )
        except Exception:
            session.rollback()
            raise

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
