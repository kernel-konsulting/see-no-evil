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
4. URL keyword deny — substring match against the full URL path/query
   → ``block(reason="deny_keyword")``.
5. YouTube channel rules (only when host is YouTube):
   * deny channel → ``block(reason="deny_youtube_channel")``
   * channel allow-list set + no match → ``block(reason="youtube_channel_not_allowed")``
6. Allow domains — if the profile has any allow entries, only matching hosts
   pass; everything else → ``block(reason="not_in_allowlist")``.
7. Classifier scores — any class whose score >= the profile's threshold (with
   fallback to global thresholds) → ``block(reason="classifier:<class>")``.
8. Default → ``allow``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Literal
from urllib.parse import unquote_plus, urlparse

from .config import AppConfig

Decision = Literal["allow", "block"]

_NON_BLOCKING_CLASSIFIER_LABELS = {"neutral", "drawing", "drawings"}


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
    # Global panic-relax override. When True, the engine short-circuits to
    # ``allow`` with reason ``"panic_relax"``.
    panic_relax: bool = False


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
    enforce_allowlist: bool = False
    # URL substring patterns (case-insensitive) matched against path+query.
    deny_url_keywords: list[str] = field(default_factory=list)
    # YouTube channel identifiers/handles (e.g. "@LinusTechTips" or "UCxxxx").
    allow_youtube_channels: list[str] = field(default_factory=list)
    deny_youtube_channels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class GlobalRules:
    allow_domains: list[str] = field(default_factory=list)
    enforce_allowlist: bool = False
    deny_domains: list[str] = field(default_factory=list)
    deny_url_keywords: list[str] = field(default_factory=list)
    apply_domain_rules: bool = True
    apply_url_rules: bool = True
    # Optional override thresholds for image classifier labels (e.g.
    # {"sexy": 0.6}). Profile thresholds still take precedence; otherwise
    # the runtime override applies, falling back to the global config.
    image_thresholds: dict[str, float] = field(default_factory=dict)


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
    return _matched_pattern(patterns, host) is not None


def _matched_pattern(patterns: list[str], host: str) -> str | None:
    for pattern in patterns:
        if _host_matches(pattern, host):
            return pattern.strip().lower().lstrip(".")
    return None


def _host_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="http")
    return (parsed.hostname or "").lower()


def _path_query_of(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="http")
    pq = parsed.path or ""
    if parsed.query:
        pq = f"{pq}?{parsed.query}"
    return pq


def _url_keyword_text(url: str) -> str:
    parsed = urlparse(url if "://" in url else f"//{url}", scheme="http")
    pieces = [url, parsed.netloc, parsed.path, parsed.query]
    return unquote_plus(" ".join(pieces)).lower()


def _matched_keyword(keywords: list[str], url: str) -> str | None:
    text = _url_keyword_text(url)
    for keyword in keywords:
        kw = keyword.strip().lower()
        if kw and kw in text:
            return kw
    return None


def _is_youtube_host(host: str) -> bool:
    h = host.lower()
    return h == "youtube.com" or h.endswith(".youtube.com") or h == "youtu.be"


def _extract_youtube_channel(url: str) -> str | None:
    """Extract a channel handle or ID from a YouTube URL path.

    Recognises:
      * /@handle
      * /channel/UCxxxxxxxx
      * /c/customname
      * /user/legacyname

    Returns the matched token (lowercase, including the leading ``@`` for
    handles) or ``None`` if no channel segment was found.
    """
    path = _path_query_of(url).split("?", 1)[0]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    first = parts[0]
    if first.startswith("@"):
        return first.lower()
    if len(parts) >= 2 and first in ("channel", "c", "user"):
        return parts[1].lower()
    return None


def _channel_matches(pattern: str, channel: str) -> bool:
    """Compare a configured channel pattern with an extracted channel token.

    Both sides are lowercased. The pattern may include or omit a leading ``@``;
    so ``LinusTechTips`` matches ``@linustechtips`` and vice versa.
    """
    p = pattern.strip().lower().lstrip("@")
    c = channel.strip().lower().lstrip("@")
    return bool(p) and bool(c) and p == c


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def decide(
    profile: ProfileView,
    inputs: DecisionInput,
    *,
    config: AppConfig | None = None,
    global_rules: GlobalRules | None = None,
) -> DecisionOutput:
    """Evaluate the policy for one request and return an allow/block decision."""
    # 0. Panic-relax — admin override that short-circuits everything below.
    if inputs.panic_relax:
        return DecisionOutput("allow", "panic_relax")

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
    global_rules = global_rules or GlobalRules()

    if global_rules.apply_domain_rules:
        if host and (match := _matched_pattern(global_rules.deny_domains, host)):
            return DecisionOutput("block", f"global_deny_domain:{match}")
        if host and (match := _matched_pattern(global_rules.allow_domains, host)):
            return DecisionOutput("allow", f"global_allow_domain:{match}")
        if (
            global_rules.enforce_allowlist
            and global_rules.allow_domains
            and (not host or not _match_any(global_rules.allow_domains, host))
        ):
            return DecisionOutput("block", f"global_not_in_allowlist:{host or 'unknown'}")

    if global_rules.apply_url_rules and (
        match := _matched_keyword(global_rules.deny_url_keywords, inputs.url)
    ):
        return DecisionOutput("block", f"global_deny_keyword:{match}")

    # 3. Deny list (always wins over allow on the same host: safer default).
    if (
        global_rules.apply_domain_rules
        and host
        and (match := _matched_pattern(profile.deny_domains, host))
    ):
        return DecisionOutput("block", f"deny_domain:{match}")

    # 4. URL keyword/substring deny (case-insensitive on path+query).
    if global_rules.apply_url_rules and (
        match := _matched_keyword(profile.deny_url_keywords, inputs.url)
    ):
        return DecisionOutput("block", f"deny_keyword:{match}")

    # 5. YouTube channel allow/deny (only when host is YouTube).
    if global_rules.apply_domain_rules and host and _is_youtube_host(host):
        channel = _extract_youtube_channel(inputs.url)
        if channel is not None:
            if any(_channel_matches(p, channel) for p in profile.deny_youtube_channels):
                return DecisionOutput("block", "deny_youtube_channel")
            if profile.allow_youtube_channels and not any(
                _channel_matches(p, channel) for p in profile.allow_youtube_channels
            ):
                return DecisionOutput("block", "youtube_channel_not_allowed")
        elif profile.allow_youtube_channels:
            # Channel allow-list set + URL is a YouTube URL with no channel
            # info (e.g. /watch?v=...): conservative → block.
            return DecisionOutput("block", "youtube_channel_not_allowed")

    # 6. Allow list (only enforced if enforce_allowlist is true)
    if (
        global_rules.apply_domain_rules
        and host
        and (match := _matched_pattern(profile.allow_domains, host))
    ):
        return DecisionOutput("allow", f"allow_domain:{match}")
    if (
        global_rules.apply_domain_rules
        and profile.enforce_allowlist
        and profile.allow_domains
        and (not host or not _match_any(profile.allow_domains, host))
    ):
        return DecisionOutput("block", f"not_in_allowlist:{host or 'unknown'}")

    # 7. Classifier thresholds
    global_image = config.classifiers.image.thresholds.model_dump() if config is not None else {}
    runtime_image = global_rules.image_thresholds
    for cls, score in inputs.classifier_scores.items():
        label = cls.split(":", 1)[1] if ":" in cls else cls
        if label in _NON_BLOCKING_CLASSIFIER_LABELS:
            continue
        threshold = profile.image_thresholds.get(label)
        if threshold is None:
            threshold = runtime_image.get(label)
        if threshold is None:
            threshold = global_image.get(label)
        if threshold is None:
            continue
        if score >= threshold:
            return DecisionOutput("block", f"classifier:{label}")

    return DecisionOutput("allow", "default")


def now_parts(dt: datetime) -> tuple[int, time, date]:
    """Convenience: split a ``datetime`` into the (dow, time, date) the engine wants."""
    return dt.weekday(), dt.timetz().replace(tzinfo=None), dt.date()
