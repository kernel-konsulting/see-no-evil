"""Shared quota day helper — pod-local timezone date."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import AppConfig


def quota_day(config: AppConfig) -> date:
    """Return today's date in the pod's local timezone (centralized quota day).

    Falls back to UTC on invalid timezone. Used by decide and quota routers
    so day boundary is consistent (F20).
    """
    try:
        tz = ZoneInfo(config.pod.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")
    return datetime.now(tz).date()


def _quota_today(config: AppConfig) -> date:
    """Alias for backwards compatibility."""
    return quota_day(config)
