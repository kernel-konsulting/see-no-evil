"""SQLAlchemy ORM models.

Schema is intentionally narrow: just enough state for M1 (profiles, devices,
audit log, daily quota counter, key/value settings). All JSON columns use
SQLAlchemy's portable ``JSON`` type so the same migration runs on SQLite and
PostgreSQL.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    image_thresholds: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    schedule: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    quota_minutes_per_day: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    allow_domains: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    deny_domains: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    allow_youtube_channels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    deny_youtube_channels: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    notify_on_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    devices: Mapped[list[Device]] = relationship(back_populates="profile")


class Device(Base):
    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mac: Mapped[str] = mapped_column(String(17), unique=True, nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    profile_id: Mapped[int] = mapped_column(
        ForeignKey("profiles.id", ondelete="RESTRICT"), nullable=False
    )
    bypass_proxy: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )

    profile: Mapped[Profile] = relationship(back_populates="devices")


class AuditDecision(Base):
    __tablename__ = "audit_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, nullable=False, index=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), nullable=True
    )
    profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(String(128), default="", nullable=False)
    classifier_scores: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)

    __table_args__ = (Index("ix_audit_device_ts", "device_id", "ts"),)


class Quota(Base):
    __tablename__ = "quotas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    minutes_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    __table_args__ = (UniqueConstraint("device_id", "day", name="uq_quota_device_day"),)


class Setting(Base):
    """Generic key/value store for install-wizard state, secrets, etc."""

    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow, nullable=False
    )
