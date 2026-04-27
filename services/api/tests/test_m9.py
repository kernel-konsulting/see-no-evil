"""M9 — alerts webhook test."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from seenoevil_api import notifications
from seenoevil_api.app import create_app
from seenoevil_api.config import (
    AllowDeny,
    AppConfig,
    DBConfig,
    DevicesConfig,
    NotificationsConfig,
    ProfileConfig,
)


@pytest.fixture
def alerts_client(tmp_db_url: str) -> TestClient:
    cfg = AppConfig(
        db=DBConfig(url=tmp_db_url),
        notifications=NotificationsConfig(
            enabled=True,
            webhook_url="https://example.com/hook",
            timeout_seconds=2,
        ),
        profiles=[ProfileConfig(name="guests", allow=AllowDeny(), deny=AllowDeny())],
        devices=DevicesConfig(default_profile="guests"),
    )
    app = create_app(cfg)
    return TestClient(app)


def test_alerts_webhook_dispatches_notification(
    alerts_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[dict[str, Any]] = []

    def fake_send(_cfg: NotificationsConfig, payload: dict[str, Any]) -> None:
        sent.append(payload)

    monkeypatch.setattr(notifications, "_send_sync", fake_send)

    r = alerts_client.post(
        "/v1/alerts/webhook",
        json={
            "alerts": [
                {
                    "status": "firing",
                    "labels": {
                        "alertname": "ProxyDown",
                        "severity": "critical",
                        "service": "proxy",
                    },
                    "annotations": {
                        "summary": "MITM proxy is not scrapeable",
                        "description": "...",
                    },
                    "startsAt": "2026-01-01T00:00:00Z",
                },
                {
                    "status": "firing",
                    "labels": {"alertname": "Other"},
                    "annotations": {},
                },
            ]
        },
    )
    assert r.status_code == 204
    assert len(sent) == 2
    assert sent[0]["event"] == "alert_firing"
    assert sent[0]["reason"] == "MITM proxy is not scrapeable"
    assert sent[0]["extra"]["severity"] == "critical"
    assert sent[1]["reason"] == "Other"


def test_alerts_webhook_noop_when_notifications_disabled(tmp_db_url: str) -> None:
    cfg = AppConfig(
        db=DBConfig(url=tmp_db_url),
        notifications=NotificationsConfig(enabled=False),
        profiles=[ProfileConfig(name="guests", allow=AllowDeny(), deny=AllowDeny())],
        devices=DevicesConfig(default_profile="guests"),
    )
    with TestClient(create_app(cfg)) as c:
        r = c.post("/v1/alerts/webhook", json={"alerts": [{"status": "firing"}]})
        assert r.status_code == 204
