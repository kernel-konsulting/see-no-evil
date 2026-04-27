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
    deny_domains: list[str] = Field(default_factory=list)
    deny_url_keywords: list[str] = Field(default_factory=list)
    allow_youtube_channels: list[str] = Field(default_factory=list)
    deny_youtube_channels: list[str] = Field(default_factory=list)
    notify_on_block: bool = False


class ProfileCreate(ProfileBase):
    pass


class ProfileUpdate(BaseModel):
    description: str | None = None
    image_thresholds: dict[str, float] | None = None
    schedule: dict[str, str] | None = None
    quota_minutes_per_day: int | None = None
    allow_domains: list[str] | None = None
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


class DecideRequest(BaseModel):
    """Body the proxy posts to ``/v1/decide``."""

    url: str
    content_type: str | None = None
    classifier_scores: dict[str, float] = Field(default_factory=dict)
    device_mac: str | None = None
    device_id: int | None = None
    # Optional small base64-encoded blurred preview, supplied by the proxy
    # for image/video responses so the quarantine queue can render thumbnails.
    thumbnail_b64: str | None = None

    @field_validator("device_mac")
    @classmethod
    def _norm_mac(cls, v: str | None) -> str | None:
        return normalize_mac(v) if v else None


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


class SetupRequest(BaseModel):
    email: str
    password: str = Field(min_length=8)


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
