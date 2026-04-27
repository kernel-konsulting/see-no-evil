"""Minimal Python policy engine.

This is a placeholder for the OPA integration that will land in a follow-up.
For M1.1 it's plenty: the proxy posts an enriched request to ``/v1/decide`` and
this module returns ``allow`` or ``block`` along with a short reason.

Decision order (first match wins):

1. Schedule check — if profile has a schedule and "now" falls outside any
   allowed window, ``block(reason="schedule")``.
2. Quota check — if profile sets ``quota_minutes_per_day`` > 0 and today's
   usage already meets/exceeds it, ``block(reason="quota")``.
3. Deny domains — exact or wildcard match on the URL host → ``block(reason="deny_domain")``.
4. Allow domains — if the profile has any allow entries, only matching hosts
   pass; everything else → ``block(reason="not_in_allowlist")``.
5. Classifier scores — any class whose score >= the profile's threshold (with
   fallback to global thresholds) → ``block(reason="classifier:<class>")``.
6. Default → ``allow``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal
from urllib.parse import urlparse

from .config import AppConfig

Decision = Literal["allow", "block"]


@dataclass(frozen=True)
class DecisionInput:
    url: str
    content_type: str | None = None
    classifier_scores: dict[str, float] = field(default_factory=dict)
    # Day-of-week (Mon=0..Sun=6) and local time, supplied by the caller so the
    # policy engine remains pure / easily testable.
    now_dow: int = 0
    now_time: time = field(default_factory=lambda: time(0, 0))
    today: date = field(default_factory=date.today)
    # Pre-fetched quota usage (minutes) for this device today.
    minutes_used_today: int = 0


@dataclass(frozen=True)
class DecisionOutput:
    decision: Decision
    reason: str = ""

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


# A tiny representation of "the profile bits the engine actually needs", so
# callers can build it from either the DB model or a config-only profile.
@dataclass(frozen=True)
class ProfileView:
    name: str
    image_thresholds: dict[str, float]
    schedule: dict[str, str]
    quota_minutes_per_day: int
    allow_domains: list[str]
    deny_domains: list[str]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Map config schedule keys to the set of weekday indices they cover.
_SCHEDULE_KEYS: dict[str, frozenset[int]] = {
    "monday": frozenset({0}),
    "tuesday": frozenset({1}),
    "wednesday": frozenset({2}),
    "thursday": frozenset({3}),
    "friday": frozenset({4}),
    "saturday": frozenset({5}),
    "sunday": frozenset({6}),
    "monday_friday": frozenset({0, 1, 2, 3, 4}),
    "weekdays": frozenset({0, 1, 2, 3, 4}),
    "saturday_sunday": frozenset({5, 6}),
    "weekend": frozenset({5, 6}),
    "everyday": frozenset({0, 1, 2, 3, 4, 5, 6}),
    "all": frozenset({0, 1, 2, 3, 4, 5, 6}),
}


def _parse_window(spec: str) -> tuple[time, time]:
    """Parse "HH:MM-HH:MM" → (start, end)."""
    start_s, end_s = spec.split("-", 1)

    def _t(s: str) -> time:
        h, m = s.strip().split(":", 1)
        return time(int(h), int(m))

    return _t(start_s), _t(end_s)


def _in_schedule(schedule: dict[str, str], dow: int, now: time) -> bool:
    """Return True if ``now`` falls inside any window applicable to ``dow``.

    An empty schedule means "no restriction" (allow).
    """
    if not schedule:
        return True
    applicable_windows: list[tuple[time, time]] = []
    for key, spec in schedule.items():
        days = _SCHEDULE_KEYS.get(key.lower())
        if days is None or dow not in days:
            continue
        try:
            applicable_windows.append(_parse_window(spec))
        except ValueError:
            continue
    if not applicable_windows:
        # Schedule mentions other days, but says nothing about today → block.
        return False
    for start, end in applicable_windows:
        if start <= end:
            if start <= now <= end:
                return True
        else:
            # Window wraps midnight (e.g. 22:00-06:00).
            if now >= start or now <= end:
                return True
    return False


def _host_matches(pattern: str, host: str) -> bool:
    """Match host against a domain pattern.

    Patterns are case-insensitive. ``*.example.com`` matches any subdomain;
    ``example.com`` matches the bare domain *and* any subdomain (a curated
    list entry for "tiktok.com" should also block "www.tiktok.com").
    """
    p = pattern.strip().lower().lstrip(".")
    h = host.strip().lower()
    if not p or not h:
        return False
    if p.startswith("*."):
        suffix = p[2:]
        return h == suffix or h.endswith("." + suffix)
    return h == p or h.endswith("." + p)


def _match_any(patterns: list[str], host: str) -> bool:
    return any(_host_matches(p, host) for p in patterns)


def _host_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="http")
    return (parsed.hostname or "").lower()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def decide(
    profile: ProfileView,
    inputs: DecisionInput,
    *,
    config: AppConfig | None = None,
) -> DecisionOutput:
    """Evaluate the policy for one request and return an allow/block decision."""
    # 1. Schedule
    if not _in_schedule(profile.schedule, inputs.now_dow, inputs.now_time):
        return DecisionOutput("block", "schedule")

    # 2. Quota
    if (
        profile.quota_minutes_per_day > 0
        and inputs.minutes_used_today >= profile.quota_minutes_per_day
    ):
        return DecisionOutput("block", "quota")

    host = _host_of(inputs.url)

    # 3. Deny list (always wins over allow on the same host: safer default).
    if host and _match_any(profile.deny_domains, host):
        return DecisionOutput("block", "deny_domain")

    # 4. Allow list (presence implies allow-list-only mode)
    if profile.allow_domains and (not host or not _match_any(profile.allow_domains, host)):
        return DecisionOutput("block", "not_in_allowlist")

    # 5. Classifier thresholds
    global_image = config.classifiers.image.thresholds.model_dump() if config is not None else {}
    for cls, score in inputs.classifier_scores.items():
        threshold = profile.image_thresholds.get(cls)
        if threshold is None:
            threshold = global_image.get(cls)
        if threshold is None:
            continue
        if score >= threshold:
            return DecisionOutput("block", f"classifier:{cls}")

    return DecisionOutput("allow", "default")


def now_parts(dt: datetime) -> tuple[int, time, date]:
    """Convenience: split a ``datetime`` into the (dow, time, date) the engine wants."""
    return dt.weekday(), dt.timetz().replace(tzinfo=None), dt.date()
