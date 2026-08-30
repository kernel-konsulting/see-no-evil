"""Pydantic request/response schemas for the v1 REST API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import normalize_mac


class ProfileBase(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = ""
    image_thresholds: dict[str, float] = Field(default_factory=dict)
    schedule: dict[str, str] = Field(default_factory=dict)
    quota_minutes_per_day: int = 0
    allow_domains: list[str] = Field(default_factory=list)
    enforce_allowlist: bool = False
    deny_domains: list[str] = Field(default_factory=list)
    deny_url_keywords: list[str] = Field(default_factory=list)
    allow_youtube_channels: list[str] = Field(default_factory=list)
    deny_youtube_channels: list[str] = Field(default_factory=list)
    notify_on_block: bool = False


class ProfileCreate(ProfileBase):
    pass


class ProfileUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None
    image_thresholds: dict[str, float] | None = None
    schedule: dict[str, str] | None = None
    quota_minutes_per_day: int | None = None
    allow_domains: list[str] | None = None
    enforce_allowlist: bool | None = None
    deny_domains: list[str] | None = None
    deny_url_keywords: list[str] | None = None
    allow_youtube_channels: list[str] | None = None
    deny_youtube_channels: list[str] | None = None
    notify_on_block: bool | None = None


class ProfileOut(ProfileBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    created_at: datetime
    updated_at: datetime


class DeviceBase(BaseModel):
    mac: str
    name: str | None = None
    profile_id: int
    bypass_proxy: bool = False

    @field_validator("mac")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_mac(v)


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    name: str | None = None
    profile_id: int | None = None
    bypass_proxy: bool | None = None


class DeviceOut(DeviceBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ip: str | None = None
    vendor: str | None = None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DiscoveredDevice(BaseModel):
    """One device observed by the scanner."""

    mac: str
    ip: str | None = None
    hostname: str | None = None
    vendor: str | None = None

    @field_validator("mac")
    @classmethod
    def _norm(cls, v: str) -> str:
        return normalize_mac(v)


class DiscoverRequest(BaseModel):
    devices: list[DiscoveredDevice] = Field(default_factory=list)


class DiscoverResponseItem(BaseModel):
    mac: str
    device_id: int
    created: bool


class DiscoverResponse(BaseModel):
    profile_id: int
    profile_name: str
    items: list[DiscoverResponseItem] = Field(default_factory=list)


class AuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: datetime
    device_id: int | None
    profile_id: int | None
    url: str
    content_type: str | None
    decision: str
    reason: str
    classifier_scores: dict[str, Any]
    thumbnail_b64: str | None = None
    # True when the row's HMAC signature verifies (None for legacy unsigned
    # rows). Lets admins spot a tampered audit log.
    signature_valid: bool | None = None


class DecideRequest(BaseModel):
    """Body the proxy posts to ``/v1/decide``."""

    url: str
    content_type: str | None = None
    classifier_scores: dict[str, float] = Field(default_factory=dict)
    device_mac: str | None = None
    device_id: int | None = None
    # Best-effort source IP captured by the proxy. The API uses it both for
    # device lookup (matches scanner-supplied Device.ip) and to synthesise a
    # device row when no MAC is available.
    client_ip: str | None = None
    # Optional explicit verdict from the proxy when a classifier has already
    # blocked/allowed the response body. The API persists it and applies
    # quarantine/notifications instead of re-deciding it as a pure policy check.
    decision: str | None = None
    reason: str | None = None
    # Optional small base64-encoded blurred preview, supplied by the proxy
    # for image/video responses so the quarantine queue can render thumbnails.
    # Bounded to avoid unbounded audit/quarantine growth (#41, #12).
    thumbnail_b64: str | None = Field(default=None, max_length=50000)

    @field_validator("device_mac")
    @classmethod
    def _norm_mac(cls, v: str | None) -> str | None:
        return normalize_mac(v) if v else None

    @field_validator("thumbnail_b64")
    @classmethod
    def _validate_thumb(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if len(v) > 50000:
            raise ValueError("thumbnail_b64 too large")
        # Validate base64 characters; allow empty string to be treated as None
        if v == "":
            return None
        import base64 as _b64

        try:
            # validate=True ensures only base64 alphabet plus padding
            _b64.b64decode(v, validate=True)
        except Exception as exc:
            raise ValueError("thumbnail_b64 must be valid base64") from exc
        return v


class DecideResponse(BaseModel):
    decision: str
    reason: str
    profile: str | None
    device_id: int | None


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    email: str


class OIDCStartResponse(BaseModel):
    authorize_url: str
    state: str


class SetupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)


class PanicSet(BaseModel):
    """Enable panic-relax mode (temporary global allow)."""

    duration_minutes: int = Field(default=60, ge=1, le=24 * 60)
    reason: str = Field(default="", max_length=256)


class PanicStatus(BaseModel):
    active: bool
    until: datetime | None = None
    reason: str = ""
    set_by: str | None = None
    set_at: datetime | None = None


class QuotaHeartbeat(BaseModel):
    """Reports active-use minutes for a device since the last heartbeat."""

    device_mac: str | None = None
    device_id: int | None = None
    # Source IP as seen by the proxy. The API resolves the device by IP when
    # no MAC/id is supplied (the proxy cannot see client MACs).
    client_ip: str | None = None
    minutes: int = Field(ge=0, le=24 * 60)

    @field_validator("device_mac")
    @classmethod
    def _norm(cls, v: str | None) -> str | None:
        return normalize_mac(v) if v else None


class QuotaStatus(BaseModel):
    device_id: int
    day: str
    minutes_used: int
    minutes_quota: int


class QuarantineOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    ts: datetime
    device_id: int | None
    profile_id: int | None
    url: str
    content_type: str | None
    reason: str
    classifier_scores: dict[str, Any]
    thumbnail_b64: str | None
    status: str
    resolved_at: datetime | None
    resolved_by: str | None
    flag_note: str | None = None
    flagged_by: str | None = None
    flagged_at: datetime | None = None


# ---------------------------------------------------------------------------
# Runtime settings — mirrors runtime.DEFAULTS (lives here for OpenAPI)
# ---------------------------------------------------------------------------


class RuntimeInspect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: bool = True
    video: bool = True
    text: bool = True
    domain: bool = True
    url: bool = True


class RuntimeLists(BaseModel):
    model_config = ConfigDict(extra="forbid")
    global_allow_domains: list[str] = Field(default_factory=list)
    enforce_global_allowlist: bool = False
    global_deny_domains: list[str] = Field(default_factory=list)
    global_deny_keywords: list[str] = Field(default_factory=list)


class RuntimeText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nsfw_threshold: float = 0.5


class RuntimeImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sexy_threshold: float = 0.6
    porn_threshold: float = 0.5
    hentai_threshold: float = 0.5


class RuntimeNotifications(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    ntfy_url: str = ""
    webhook_url: str = ""
    webhook_token: str = ""
    on_block: bool = True
    on_quarantine: bool = True
    on_panic: bool = True


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inspect: RuntimeInspect = Field(default_factory=RuntimeInspect)
    lists: RuntimeLists = Field(default_factory=RuntimeLists)
    text: RuntimeText = Field(default_factory=RuntimeText)
    image: RuntimeImage = Field(default_factory=RuntimeImage)
    notifications: RuntimeNotifications = Field(default_factory=RuntimeNotifications)


class RuntimePatch(BaseModel):
    """Partial update for PUT /v1/settings. Extra top-level keys are forbidden."""

    model_config = ConfigDict(extra="forbid")
    inspect: RuntimeInspect | None = None
    lists: RuntimeLists | None = None
    text: RuntimeText | None = None
    image: RuntimeImage | None = None
    notifications: RuntimeNotifications | None = None
