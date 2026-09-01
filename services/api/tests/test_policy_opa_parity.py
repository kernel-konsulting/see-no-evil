# ruff: noqa: E501
"""Parity harness: Python policy vs OPA Rego (M2.1).

Feeds the same matrix to both engines and fails on decision/reason divergence.
OPA is evaluated via ``opa eval`` subprocess when available; otherwise the
test is skipped with a clear message so CI without opa still passes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from datetime import date, time
from pathlib import Path
from typing import Any

import pytest
from seenoevil_api.config import AppConfig
from seenoevil_api.policy import DecisionInput, GlobalRules, ProfileView, decide
from seenoevil_api.policy_opa import build_opa_input


def _opa_available() -> str | None:
    for c in ("opa", "/tmp/opa", "/usr/local/bin/opa"):
        if shutil.which(c) or Path(c).exists():
            return c
    return None


REPO_ROOT = Path(__file__).resolve().parents[3]
REGO_PATH = REPO_ROOT / "policies" / "seenoevil.rego"


def _eval_rego(opa_input: dict[str, Any]) -> dict[str, str]:
    opa_bin = _opa_available()
    if not opa_bin or not REGO_PATH.exists():
        pytest.skip(f"opa binary or rego not found ({opa_bin}, {REGO_PATH})")
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(opa_input, f)
        fname = f.name
    try:
        out = subprocess.run(
            [
                opa_bin,
                "eval",
                "--data",
                str(REGO_PATH),
                "--input",
                fname,
                "data.seenoevil.policy.decision",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=10,
        )
    finally:
        Path(fname).unlink(missing_ok=True)
    if out.returncode != 0:
        pytest.fail(f"opa eval failed: {out.stderr[:1000]}")
    data = json.loads(out.stdout)
    val = data["result"][0]["expressions"][0]["value"]
    return {"decision": str(val.get("decision", "")), "reason": str(val.get("reason", ""))}


def _case(
    *,
    name: str,
    profile: ProfileView,
    inp: DecisionInput,
    global_rules: GlobalRules | None = None,
    config: AppConfig | None = None,
) -> tuple[str, ProfileView, DecisionInput, GlobalRules, AppConfig]:
    return (name, profile, inp, global_rules or GlobalRules(), config or AppConfig())


def _profile(**overrides) -> ProfileView:
    base = dict(
        name="test",
        image_thresholds={"porn": 0.5},
        schedule={},
        quota_minutes_per_day=0,
        allow_domains=[],
        deny_domains=[],
        enforce_allowlist=False,
    )
    base.update(overrides)
    return ProfileView(**base)


def _inp(**overrides) -> DecisionInput:
    base = dict(
        url="https://example.com/x",
        classifier_scores={},
        now_dow=2,
        now_time=time(12, 0),
        today=date(2026, 4, 22),
        minutes_used_today=0,
    )
    base.update(overrides)
    return DecisionInput(**base)


# Matrix ~30 cases covering schedule, quota, deny, keyword, youtube, allowlist, classifier, global, edge
CASES = [
    _case(name="default_allow", profile=_profile(), inp=_inp()),
    _case(
        name="deny_domain",
        profile=_profile(deny_domains=["tiktok.com"]),
        inp=_inp(url="https://www.tiktok.com/foo"),
    ),
    _case(
        name="deny_wildcard",
        profile=_profile(deny_domains=["*.evil.test"]),
        inp=_inp(url="https://sub.evil.test/page"),
    ),
    _case(
        name="allowlist_off_allows",
        profile=_profile(allow_domains=["khanacademy.org"]),
        inp=_inp(url="https://example.com/"),
    ),
    _case(
        name="allowlist_enforced_blocks",
        profile=_profile(allow_domains=["khanacademy.org"], enforce_allowlist=True),
        inp=_inp(url="https://example.com/"),
    ),
    _case(
        name="allowlist_match_allows",
        profile=_profile(allow_domains=["khanacademy.org"], enforce_allowlist=True),
        inp=_inp(url="https://khanacademy.org/x"),
    ),
    _case(
        name="allowlist_skips_classifier",
        profile=_profile(allow_domains=["khanacademy.org"], image_thresholds={"porn": 0.4}),
        inp=_inp(url="https://khanacademy.org/lesson", classifier_scores={"porn": 0.99}),
    ),
    _case(
        name="deny_wins_over_allow",
        profile=_profile(allow_domains=["example.com"], deny_domains=["example.com"]),
        inp=_inp(url="https://example.com/"),
    ),
    _case(
        name="keyword_block",
        profile=_profile(deny_url_keywords=["nsfw"]),
        inp=_inp(url="https://example.com/category/Nsfw/page"),
    ),
    _case(
        name="keyword_query",
        profile=_profile(deny_url_keywords=["xxx"]),
        inp=_inp(url="https://search.example/?q=xxx"),
    ),
    _case(
        name="keyword_encoded",
        profile=_profile(deny_url_keywords=["hello world"]),
        inp=_inp(url="https://example.com/search?q=hello%20world"),
    ),
    _case(
        name="keyword_no_match",
        profile=_profile(deny_url_keywords=["nsfw"]),
        inp=_inp(url="https://example.com/news"),
    ),
    _case(
        name="quota_blocks",
        profile=_profile(quota_minutes_per_day=60),
        inp=_inp(minutes_used_today=60),
    ),
    _case(
        name="quota_unlimited",
        profile=_profile(quota_minutes_per_day=0),
        inp=_inp(minutes_used_today=10000),
    ),
    _case(
        name="schedule_inside",
        profile=_profile(schedule={"monday_friday": "07:00-20:00"}),
        inp=_inp(now_dow=2, now_time=time(10, 0)),
    ),
    _case(
        name="schedule_outside",
        profile=_profile(schedule={"monday_friday": "07:00-20:00"}),
        inp=_inp(now_dow=2, now_time=time(22, 0)),
    ),
    _case(
        name="schedule_unspecified_day",
        profile=_profile(schedule={"monday_friday": "07:00-20:00"}),
        inp=_inp(now_dow=5, now_time=time(10, 0)),
    ),
    _case(
        name="schedule_wrap_allow",
        profile=_profile(schedule={"everyday": "22:00-06:00"}),
        inp=_inp(now_time=time(23, 0)),
    ),
    _case(
        name="schedule_wrap_block",
        profile=_profile(schedule={"everyday": "22:00-06:00"}),
        inp=_inp(now_time=time(12, 0)),
    ),
    _case(
        name="youtube_deny",
        profile=_profile(deny_youtube_channels=["@badchannel"]),
        inp=_inp(url="https://www.youtube.com/@badchannel/videos"),
    ),
    _case(
        name="youtube_allowlist_blocks",
        profile=_profile(allow_youtube_channels=["@kidsapproved"]),
        inp=_inp(url="https://www.youtube.com/@randomchannel"),
    ),
    _case(
        name="youtube_allowlist_allows",
        profile=_profile(allow_youtube_channels=["KidsApproved"]),
        inp=_inp(url="https://www.youtube.com/@kidsapproved/videos"),
    ),
    _case(
        name="youtube_watch_blocks",
        profile=_profile(allow_youtube_channels=["@kidsapproved"]),
        inp=_inp(url="https://www.youtube.com/watch?v=abc"),
    ),
    _case(
        name="non_youtube_unaffected",
        profile=_profile(deny_youtube_channels=["@bad"]),
        inp=_inp(url="https://example.com/@bad"),
    ),
    _case(
        name="classifier_block",
        profile=_profile(image_thresholds={"porn": 0.4}),
        inp=_inp(classifier_scores={"porn": 0.5}),
    ),
    _case(
        name="classifier_allow_below",
        profile=_profile(image_thresholds={"porn": 0.6}),
        inp=_inp(classifier_scores={"porn": 0.5}),
    ),
    _case(
        name="neutral_not_blocking",
        profile=_profile(image_thresholds={"porn": 0.5, "neutral": 0.1}),
        inp=_inp(classifier_scores={"neutral": 0.99}),
    ),
    _case(
        name="drawing_not_blocking",
        profile=_profile(image_thresholds={"porn": 0.5, "drawing": 0.1}),
        inp=_inp(classifier_scores={"drawing": 0.9}),
    ),
    _case(
        name="image_prefix_stripped",
        profile=_profile(image_thresholds={"porn": 0.4}),
        inp=_inp(classifier_scores={"image:porn": 0.5}),
    ),
    _case(
        name="global_deny",
        profile=_profile(),
        inp=_inp(url="https://evil.com/"),
        global_rules=GlobalRules(deny_domains=["evil.com"]),
    ),
    _case(
        name="global_allow",
        profile=_profile(),
        inp=_inp(url="https://good.com/"),
        global_rules=GlobalRules(allow_domains=["good.com"]),
    ),
    _case(
        name="global_keyword",
        profile=_profile(),
        inp=_inp(url="https://example.com/?q=badword"),
        global_rules=GlobalRules(deny_url_keywords=["badword"]),
    ),
    _case(
        name="threshold_fallback_global",
        profile=_profile(image_thresholds={}),
        inp=_inp(classifier_scores={"porn": 0.7}),
        global_rules=GlobalRules(image_thresholds={"porn": 0.5}),
    ),
]

# Also test when global apply flags are off — domain rules should be ignored
CASES.append(
    _case(
        name="global_domain_disabled",
        profile=_profile(),
        inp=_inp(url="https://evil.com/"),
        global_rules=GlobalRules(deny_domains=["evil.com"], apply_domain_rules=False),
    )
)


@pytest.mark.parametrize("name,profile,inp,global_rules,config", CASES, ids=[c[0] for c in CASES])
def test_opa_parity(
    name: str,
    profile: ProfileView,
    inp: DecisionInput,
    global_rules: GlobalRules,
    config: AppConfig,
) -> None:
    py_out = decide(profile, inp, config=config, global_rules=global_rules)
    opa_input = build_opa_input(profile, inp, config=config, global_rules=global_rules)
    opa_out = _eval_rego(opa_input)
    assert (
        opa_out["decision"] == py_out.decision
    ), f"{name}: decision mismatch py={py_out.decision} opa={opa_out['decision']} (reason py={py_out.reason} opa={opa_out['reason']})"
    assert (
        opa_out["reason"] == py_out.reason
    ), f"{name}: reason mismatch py={py_out.reason!r} opa={opa_out['reason']!r} decision={py_out.decision}"


# ---------------------------------------------------------------------------
# API integration — OPA engine selection and fallback (mocked httpx)
# ---------------------------------------------------------------------------


def test_decide_api_uses_opa_when_engine_opa(monkeypatch) -> None:
    """POST /v1/decide with policy.engine=opa calls the OPA sidecar."""
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient
    from seenoevil_api.app import create_app
    from seenoevil_api.config import (
        AllowDeny,
        AppConfig,
        DBConfig,
        DevicesConfig,
        PolicyConfig,
        ProfileConfig,
    )

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        cfg = AppConfig(
            db=DBConfig(url=f"sqlite:///{path}"),
            policy=PolicyConfig(engine="opa", opa_url="http://opa:8181", opa_timeout_ms=1500),
            profiles=[
                ProfileConfig(
                    name="kids", deny=AllowDeny(domains=["tiktok.com"]), allow=AllowDeny(domains=[])
                ),
                ProfileConfig(
                    name="guests", allow=AllowDeny(domains=[]), deny=AllowDeny(domains=[])
                ),
            ],
            devices=DevicesConfig(default_profile="guests"),
        )
        # Mock httpx.Client to return OPA decision without network
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "result": {"decision": "block", "reason": "deny_domain:tiktok.com"}
        }
        mock_resp.text = ""
        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.return_value = mock_resp

        import seenoevil_api.policy_opa as opa_mod

        monkeypatch.setattr(opa_mod.httpx, "Client", lambda timeout: mock_client)

        app = create_app(cfg)
        with TestClient(app):
            # need proxy token header (default test token)
            # Use the app's config proxy token fallback: set via config proxy.api_token
            # Instead we pass the app's expected token via headers
            # The test config has no proxy token, so we set env override
            from conftest import PROXY_TOKEN

            # Recreate with token
            cfg.proxy.api_token = PROXY_TOKEN
            app2 = create_app(cfg)
            with TestClient(app2) as c2:
                r = c2.post(
                    "/v1/decide",
                    json={"url": "https://www.tiktok.com/foo", "client_ip": "10.0.0.5"},
                    headers={"Authorization": f"Bearer {PROXY_TOKEN}"},
                )
                assert r.status_code == 200, r.text
                assert r.json()["decision"] == "block"
                assert "deny_domain" in r.json()["reason"]
                # Verify OPA was called
                assert mock_client.post.called
    finally:
        Path(path).unlink(missing_ok=True)


def test_decide_auto_fallback_when_opa_unavailable(monkeypatch) -> None:
    """policy.engine=auto falls back to Python on OPA error."""
    import tempfile
    from pathlib import Path
    from unittest.mock import MagicMock

    from fastapi.testclient import TestClient

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        from seenoevil_api.config import (
            AllowDeny,
            AppConfig,
            DBConfig,
            DevicesConfig,
            PolicyConfig,
            ProfileConfig,
        )

        cfg = AppConfig(
            db=DBConfig(url=f"sqlite:///{path}"),
            policy=PolicyConfig(engine="auto", opa_url="http://opa:8181", opa_timeout_ms=500),
            profiles=[
                ProfileConfig(name="kids", deny=AllowDeny(domains=[]), allow=AllowDeny(domains=[])),
                ProfileConfig(
                    name="guests",
                    deny=AllowDeny(domains=["tiktok.com"]),
                    allow=AllowDeny(domains=[]),
                ),
            ],
            devices=DevicesConfig(default_profile="guests"),
        )
        import httpx as httpx_mod
        import seenoevil_api.policy_opa as opa_mod

        def _raise(*args, **kwargs):
            raise httpx_mod.ConnectError("opa down")

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.post.side_effect = _raise
        monkeypatch.setattr(opa_mod.httpx, "Client", lambda timeout: mock_client)

        from conftest import PROXY_TOKEN

        cfg.proxy.api_token = PROXY_TOKEN
        from seenoevil_api.app import create_app

        app = create_app(cfg)
        with TestClient(app) as c:
            r = c.post(
                "/v1/decide",
                json={"url": "https://www.tiktok.com/foo", "client_ip": "10.0.0.6"},
                headers={"Authorization": f"Bearer {PROXY_TOKEN}"},
            )
            assert r.status_code == 200, r.text
            # Fallback to Python should still block
            assert r.json()["decision"] == "block"
            assert "deny_domain" in r.json()["reason"]
    finally:
        Path(path).unlink(missing_ok=True)
