"""Configuration loading and validation.

The on-disk format is YAML (see ``config.example.yaml`` at the repo root).
This module parses it once at startup, validates it against a Pydantic schema,
and exposes the resulting ``AppConfig`` to the rest of the application.

Only the keys the API service actually consumes are modelled in detail. Other
sections (e.g. ``proxy``, ``dns``, ``classifiers``) are accepted but kept as
loosely-typed sub-models so downstream services can read the same file without
this module needing to know every detail.
"""

from __future__ import annotations

import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_DURATION_RE = re.compile(r"^\s*(\d+)\s*(s|m|h|d)\s*$")
_SIZE_RE = re.compile(r"^\s*(\d+)\s*(B|KiB|MiB|GiB|TiB)\s*$", re.IGNORECASE)


def parse_duration_seconds(value: str | int) -> int:
    """Parse a Go-style duration string ("30s", "5m", "24h", "7d") to seconds."""
    if isinstance(value, int):
        return value
    m = _DURATION_RE.match(str(value))
    if not m:
        raise ValueError(f"invalid duration: {value!r}")
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def parse_size_bytes(value: str | int) -> int:
    """Parse an IEC size string ("10MiB", "2GiB") to bytes."""
    if isinstance(value, int):
        return value
    m = _SIZE_RE.match(str(value))
    if not m:
        raise ValueError(f"invalid size: {value!r}")
    n, unit = int(m.group(1)), m.group(2).lower()
    mult = {
        "b": 1,
        "kib": 1024,
        "mib": 1024**2,
        "gib": 1024**3,
        "tib": 1024**4,
    }[unit]
    return n * mult


# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow")


class PodConfig(_Base):
    hostname: str = "seenoevil.lan"
    data_dir: str = "/data"
    timezone: str = "UTC"


class DBConfig(_Base):
    url: str = "sqlite:///data/policy.db"
    pool_size: int = 5
    max_overflow: int = 10

    @field_validator("url")
    @classmethod
    def _check_url(cls, v: str) -> str:
        if not (v.startswith("sqlite:") or v.startswith("postgresql")):
            raise ValueError(
                "db.url must start with 'sqlite:' or 'postgresql' (e.g. postgresql+psycopg://...)"
            )
        return v


class CacheConfig(_Base):
    kind: Literal["embedded", "redis"] = "embedded"
    url: str | None = None
    password_file: str | None = None

    @model_validator(mode="after")
    def _check_redis(self) -> CacheConfig:
        if self.kind == "redis" and not self.url:
            raise ValueError("cache.url is required when cache.kind == 'redis'")
        return self


class ImageThresholds(_Base):
    porn: float = 0.60
    hentai: float = 0.60
    sexy: float = 0.85
    neutral: float = 1.01
    drawing: float = 1.01


class TextThresholds(_Base):
    toxic: float = 0.80
    obscene: float = 0.80
    threat: float = 0.80


class ImageClassifierConfig(_Base):
    model: str = "freepik"
    device: str = "cpu"
    thresholds: ImageThresholds = Field(default_factory=ImageThresholds)


class TextClassifierConfig(_Base):
    model: str = "unitary/toxic-bert"
    device: str = "cpu"
    thresholds: TextThresholds = Field(default_factory=TextThresholds)


class VideoClassifierConfig(_Base):
    sample_frames: int = 8
    max_video_size: str = "50MiB"


class ClassifiersConfig(_Base):
    image: ImageClassifierConfig = Field(default_factory=ImageClassifierConfig)
    text: TextClassifierConfig = Field(default_factory=TextClassifierConfig)
    video: VideoClassifierConfig = Field(default_factory=VideoClassifierConfig)


class AllowDeny(_Base):
    domains: list[str] = Field(default_factory=list)
    url_keywords: list[str] = Field(default_factory=list)
    youtube_channels: list[str] = Field(default_factory=list)


class ProfileConfig(_Base):
    name: str
    description: str = ""
    image_thresholds: dict[str, float] = Field(default_factory=dict)
    schedule: dict[str, str] = Field(default_factory=dict)
    quota_minutes_per_day: int = 0
    allow: AllowDeny = Field(default_factory=AllowDeny)
    deny: AllowDeny = Field(default_factory=AllowDeny)
    notify_on_block: bool = False

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("profile.name must be non-empty")
        return v.strip()


class StaticDevice(_Base):
    mac: str
    name: str | None = None
    profile: str
    bypass_proxy: bool = False

    @field_validator("mac")
    @classmethod
    def _normalize_mac(cls, v: str) -> str:
        return normalize_mac(v)


class DevicesConfig(_Base):
    default_profile: str = "guests"
    static: list[StaticDevice] = Field(default_factory=list)

    @field_validator("static", mode="before")
    @classmethod
    def _none_is_empty(cls, v):
        return [] if v is None else v


class AuthBuiltinConfig(_Base):
    admin_email: str = "admin@example.local"


class AuthConfig(_Base):
    builtin: AuthBuiltinConfig = Field(default_factory=AuthBuiltinConfig)
    # OIDC and WebAuthn are accepted but not consumed by M1.1.


class AuditObservabilityConfig(_Base):
    retention_days: int = 30


class ObservabilityConfig(_Base):
    level: Literal["minimal", "full"] = "minimal"
    audit: AuditObservabilityConfig = Field(default_factory=AuditObservabilityConfig)


class ScannerConfig(_Base):
    enabled: bool = False
    cidr: str = "192.168.1.0/24"
    interval: str = "1h"


class AppConfig(_Base):
    pod: PodConfig = Field(default_factory=PodConfig)
    db: DBConfig = Field(default_factory=DBConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    classifiers: ClassifiersConfig = Field(default_factory=ClassifiersConfig)
    profiles: list[ProfileConfig] = Field(default_factory=list)
    devices: DevicesConfig = Field(default_factory=DevicesConfig)
    observability: ObservabilityConfig = Field(default_factory=ObservabilityConfig)
    scanner: ScannerConfig = Field(default_factory=ScannerConfig)

    @model_validator(mode="after")
    def _check_profiles_unique(self) -> AppConfig:
        names = [p.name for p in self.profiles]
        if len(names) != len(set(names)):
            raise ValueError("profiles[].name values must be unique")
        if self.profiles and self.devices.default_profile not in names:
            raise ValueError(
                f"devices.default_profile {self.devices.default_profile!r} "
                f"does not match any profiles[].name"
            )
        for d in self.devices.static:
            if names and d.profile not in names:
                raise ValueError(f"static device {d.mac} references unknown profile {d.profile!r}")
        return self


# ---------------------------------------------------------------------------
# MAC normalization (shared with models)
# ---------------------------------------------------------------------------


_MAC_RE = re.compile(r"^[0-9a-f]{2}(?::[0-9a-f]{2}){5}$")


def normalize_mac(mac: str) -> str:
    """Lowercase + colon-separated MAC, accepting common input formats."""
    if not isinstance(mac, str):
        raise ValueError("mac must be a string")
    s = mac.strip().lower().replace("-", ":").replace(".", "")
    # Re-insert colons if the user passed bare hex (e.g. "aabbccddeeff").
    if ":" not in s and len(s) == 12:
        s = ":".join(s[i : i + 2] for i in range(0, 12, 2))
    if not _MAC_RE.match(s):
        raise ValueError(f"invalid MAC address: {mac!r}")
    return s


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


CONFIG_ENV_VAR = "SEENOEVIL_CONFIG"


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load and validate ``config.yaml`` from ``path`` (or ``$SEENOEVIL_CONFIG``).

    If no path is provided and the env var is unset, returns ``AppConfig()`` with
    all defaults — handy for tests and ephemeral runs.
    """
    if path is None:
        env_path = os.environ.get(CONFIG_ENV_VAR)
        if not env_path:
            return AppConfig()
        path = env_path
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"config file not found: {p}")
    raw: dict[str, Any] = yaml.safe_load(p.read_text()) or {}
    return AppConfig.model_validate(raw)


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """Process-wide cached config; cleared in tests via ``get_config.cache_clear()``."""
    return load_config()
