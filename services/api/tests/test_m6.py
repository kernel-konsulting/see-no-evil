"""M6 — panic-relax, quota heartbeat, and notification fan-out."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from seenoevil_api.app import create_app
from seenoevil_api.config import (
    AllowDeny,
    AppConfig,
    DBConfig,
    DevicesConfig,
    NotificationsConfig,
    ProfileConfig,
)

# ---------------------------------------------------------------------------
# Panic-relax
# ---------------------------------------------------------------------------


def test_panic_get_default_inactive(admin_client: TestClient) -> None:
    r = admin_client.get("/v1/admin/panic")
    assert r.status_code == 200
    assert r.json()["active"] is False


def test_panic_requires_admin_to_set(client: TestClient) -> None:
    assert client.get("/v1/admin/panic").status_code == 401
    assert client.post("/v1/admin/panic", json={"duration_minutes": 5}).status_code == 401
    assert client.delete("/v1/admin/panic").status_code == 401


def test_panic_enable_then_decide_allows_blocked_domain(admin_client: TestClient) -> None:
    profiles = admin_client.get("/v1/profiles").json()
    kids_id = next(p["id"] for p in profiles if p["name"] == "kids")
    dev = admin_client.post(
        "/v1/devices", json={"mac": "aa:bb:cc:dd:ee:02", "profile_id": kids_id}
    ).json()

    # Sanity: kids profile blocks tiktok.com
    blocked = admin_client.post(
        "/v1/decide",
        json={"url": "https://www.tiktok.com/x", "device_id": dev["id"]},
    ).json()
    assert blocked["decision"] == "block"

    # Enable panic
    on = admin_client.post(
        "/v1/admin/panic", json={"duration_minutes": 30, "reason": "homework"}
    ).json()
    assert on["active"] is True
    assert on["reason"] == "homework"
    assert on["until"]
    assert on["set_by"]

    # Now the same request is allowed with reason=panic_relax
    after = admin_client.post(
        "/v1/decide",
        json={"url": "https://www.tiktok.com/x", "device_id": dev["id"]},
    ).json()
    assert after["decision"] == "allow"
    assert after["reason"] == "panic_relax"

    # Disable
    off = admin_client.delete("/v1/admin/panic").json()
    assert off["active"] is False

    # Block resumes
    again = admin_client.post(
        "/v1/decide",
        json={"url": "https://www.tiktok.com/x", "device_id": dev["id"]},
    ).json()
    assert again["decision"] == "block"


# ---------------------------------------------------------------------------
# Quota heartbeat
# ---------------------------------------------------------------------------


def test_quota_heartbeat_unknown_device_404(client: TestClient) -> None:
    r = client.post(
        "/v1/quota/heartbeat",
        json={"device_mac": "ff:ff:ff:ff:ff:fe", "minutes": 5},
    )
    assert r.status_code == 404


def test_quota_heartbeat_increments_and_blocks(admin_client: TestClient) -> None:
    profiles = admin_client.get("/v1/profiles").json()
    # Update kids profile to a tight quota
    kids_id = next(p["id"] for p in profiles if p["name"] == "kids")
    admin_client.patch(f"/v1/profiles/{kids_id}", json={"quota_minutes_per_day": 10})
    dev = admin_client.post(
        "/v1/devices", json={"mac": "aa:bb:cc:dd:ee:03", "profile_id": kids_id}
    ).json()

    # Two heartbeats accumulate.
    r1 = admin_client.post(
        "/v1/quota/heartbeat", json={"device_id": dev["id"], "minutes": 6}
    ).json()
    assert r1["minutes_used"] == 6
    assert r1["minutes_quota"] == 10

    r2 = admin_client.post(
        "/v1/quota/heartbeat", json={"device_id": dev["id"], "minutes": 5}
    ).json()
    assert r2["minutes_used"] == 11

    # Quota now exceeded → decide blocks with reason=quota.
    decision = admin_client.post(
        "/v1/decide",
        json={"url": "https://example.com", "device_id": dev["id"]},
    ).json()
    assert decision["decision"] == "block"
    assert decision["reason"] == "quota"

    # Admin GET returns the current usage.
    status = admin_client.get(f"/v1/quota/{dev['id']}").json()
    assert status["minutes_used"] == 11

    # Reset.
    assert admin_client.delete(f"/v1/quota/{dev['id']}").status_code == 204
    after = admin_client.get(f"/v1/quota/{dev['id']}").json()
    assert after["minutes_used"] == 0


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------


@pytest.fixture
def notify_config(tmp_db_url: str) -> AppConfig:
    return AppConfig(
        db=DBConfig(url=tmp_db_url),
        profiles=[
            ProfileConfig(
                name="kids",
                description="seeded",
                image_thresholds={"porn": 0.4},
                schedule={},
                quota_minutes_per_day=0,
                allow=AllowDeny(domains=[]),
                deny=AllowDeny(domains=["tiktok.com"]),
                notify_on_block=True,
            ),
            ProfileConfig(
                name="guests",
                description="default",
                schedule={},
                allow=AllowDeny(),
                deny=AllowDeny(),
            ),
        ],
        devices=DevicesConfig(default_profile="guests"),
        notifications=NotificationsConfig(
            ntfy_url="http://ntfy.test/topic",
            webhook_url="http://hook.test/cb",
            webhook_token="t0k",
        ),
    )


@pytest.fixture
def notify_client(notify_config: AppConfig) -> Iterator[TestClient]:
    app = create_app(notify_config)
    with TestClient(app) as c:
        c.post("/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"})
        c.post("/v1/auth/login", json={"email": "admin@example.local", "password": "hunter22!"})
        yield c


def test_notification_sent_on_block(
    notify_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, **kwargs: Any) -> Any:
        sent.append({"url": url, **kwargs})

        class _R:
            status_code = 200

        return _R()

    monkeypatch.setattr("seenoevil_api.notifications.httpx.post", fake_post)

    profiles = notify_client.get("/v1/profiles").json()
    kids_id = next(p["id"] for p in profiles if p["name"] == "kids")
    dev = notify_client.post(
        "/v1/devices", json={"mac": "aa:bb:cc:dd:ee:04", "profile_id": kids_id}
    ).json()

    r = notify_client.post(
        "/v1/decide",
        json={"url": "https://www.tiktok.com/x", "device_id": dev["id"]},
    )
    assert r.json()["decision"] == "block"

    urls = [s["url"] for s in sent]
    assert "http://ntfy.test/topic" in urls
    assert "http://hook.test/cb" in urls
    hook = next(s for s in sent if s["url"] == "http://hook.test/cb")
    assert hook["headers"]["Authorization"] == "Bearer t0k"
    body = hook["json"]
    assert body["event"] == "block"
    assert body["reason"] == "deny_domain"


def test_notification_skipped_when_no_targets(
    admin_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Default ``base_config`` has no ntfy/webhook URLs → no HTTP."""
    sent: list[Any] = []
    monkeypatch.setattr(
        "seenoevil_api.notifications.httpx.post",
        lambda *a, **kw: sent.append((a, kw)),
    )

    profiles = admin_client.get("/v1/profiles").json()
    kids_id = next(p["id"] for p in profiles if p["name"] == "kids")
    # notify_on_block is False on the seeded kids profile, but flip it on:
    admin_client.patch(f"/v1/profiles/{kids_id}", json={"notify_on_block": True})
    dev = admin_client.post(
        "/v1/devices", json={"mac": "aa:bb:cc:dd:ee:05", "profile_id": kids_id}
    ).json()
    admin_client.post(
        "/v1/decide",
        json={"url": "https://www.tiktok.com/x", "device_id": dev["id"]},
    )
    assert sent == []


def test_panic_change_notifies(notify_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "seenoevil_api.notifications.httpx.post",
        lambda url, **kw: sent.append({"url": url, **kw}) or type("R", (), {"status_code": 200})(),
    )
    notify_client.post("/v1/admin/panic", json={"duration_minutes": 10, "reason": "drill"})
    notify_client.delete("/v1/admin/panic")
    events = [s["json"]["event"] for s in sent if "json" in s]
    assert "panic_enabled" in events
    assert "panic_disabled" in events
