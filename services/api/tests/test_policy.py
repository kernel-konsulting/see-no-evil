"""Tests for the policy engine in isolation (no DB / FastAPI)."""

from __future__ import annotations

from datetime import date, time

from seenoevil_api.policy import DecisionInput, ProfileView, decide


def _profile(**overrides) -> ProfileView:
    base = dict(
        name="test",
        image_thresholds={"porn": 0.5},
        schedule={},
        quota_minutes_per_day=0,
        allow_domains=[],
        deny_domains=[],
    )
    base.update(overrides)
    return ProfileView(**base)


def _inp(**overrides) -> DecisionInput:
    base = dict(
        url="https://example.com/x",
        content_type="text/html",
        classifier_scores={},
        now_dow=2,  # Wednesday
        now_time=time(12, 0),
        today=date(2026, 4, 22),
        minutes_used_today=0,
    )
    base.update(overrides)
    return DecisionInput(**base)


def test_default_allow() -> None:
    out = decide(_profile(), _inp())
    assert out.decision == "allow"


def test_deny_domain_blocks() -> None:
    out = decide(
        _profile(deny_domains=["tiktok.com"]),
        _inp(url="https://www.tiktok.com/foo"),
    )
    assert out.decision == "block"
    assert out.reason == "deny_domain"


def test_wildcard_deny() -> None:
    out = decide(
        _profile(deny_domains=["*.evil.test"]),
        _inp(url="https://sub.evil.test/page"),
    )
    assert out.decision == "block"


def test_allow_list_is_exclusive() -> None:
    p = _profile(allow_domains=["khanacademy.org"])
    assert decide(p, _inp(url="https://khanacademy.org")).decision == "allow"
    assert decide(p, _inp(url="https://www.khanacademy.org/x")).decision == "allow"
    out = decide(p, _inp(url="https://example.com/"))
    assert out.decision == "block"
    assert out.reason == "not_in_allowlist"


def test_classifier_threshold_blocks() -> None:
    out = decide(
        _profile(image_thresholds={"porn": 0.4}),
        _inp(classifier_scores={"porn": 0.5}),
    )
    assert out.decision == "block"
    assert out.reason == "classifier:porn"


def test_classifier_below_threshold_allows() -> None:
    out = decide(
        _profile(image_thresholds={"porn": 0.6}),
        _inp(classifier_scores={"porn": 0.5}),
    )
    assert out.decision == "allow"


def test_quota_blocks_when_exceeded() -> None:
    out = decide(
        _profile(quota_minutes_per_day=60),
        _inp(minutes_used_today=60),
    )
    assert out.decision == "block"
    assert out.reason == "quota"


def test_quota_zero_means_unlimited() -> None:
    out = decide(_profile(quota_minutes_per_day=0), _inp(minutes_used_today=10_000))
    assert out.decision == "allow"


def test_schedule_window_allows_inside() -> None:
    out = decide(
        _profile(schedule={"monday_friday": "07:00-20:00"}),
        _inp(now_dow=2, now_time=time(10, 0)),
    )
    assert out.decision == "allow"


def test_schedule_window_blocks_outside() -> None:
    out = decide(
        _profile(schedule={"monday_friday": "07:00-20:00"}),
        _inp(now_dow=2, now_time=time(22, 0)),
    )
    assert out.decision == "block"
    assert out.reason == "schedule"


def test_schedule_blocks_unspecified_day() -> None:
    # Schedule mentions weekdays only; weekend is implicitly blocked.
    out = decide(
        _profile(schedule={"monday_friday": "07:00-20:00"}),
        _inp(now_dow=5, now_time=time(10, 0)),  # Saturday
    )
    assert out.decision == "block"


def test_schedule_wraps_midnight() -> None:
    p = _profile(schedule={"everyday": "22:00-06:00"})
    assert decide(p, _inp(now_time=time(23, 0))).decision == "allow"
    assert decide(p, _inp(now_time=time(5, 0))).decision == "allow"
    assert decide(p, _inp(now_time=time(12, 0))).decision == "block"


def test_deny_wins_over_allow_list() -> None:
    p = _profile(allow_domains=["example.com"], deny_domains=["example.com"])
    out = decide(p, _inp(url="https://example.com/"))
    assert out.decision == "block"
    assert out.reason == "deny_domain"
