"""Tests for the security fixes: proxy/scanner token auth, rate limiting,
audit HMAC signatures, retention cleanup, and IP-based device attribution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from conftest import SCANNER_TOKEN  # noqa: E402
from fastapi.testclient import TestClient
from seenoevil_api.cleanup import cleanup_expired
from seenoevil_api.models import AuditDecision, Base, Device, Profile, QuarantineItem
from seenoevil_api.routers.decide import _synthetic_mac_from_ip
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def db_session(base_config) -> Session:
    engine = create_engine(base_config.db.url)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    s = factory()
    yield s
    s.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# Proxy token auth on internal endpoints
# ---------------------------------------------------------------------------


def test_decide_requires_proxy_token(client: TestClient) -> None:
    payload = {"url": "https://example.com/", "content_type": "text/html"}
    r = client.post("/v1/decide", json=payload, headers={"Authorization": ""})
    assert r.status_code == 401
    r = client.post("/v1/decide", json=payload, headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401
    r = client.post("/v1/decide", json=payload)
    assert r.status_code == 200, r.text


def test_runtime_requires_proxy_token(client: TestClient) -> None:
    r = client.get("/v1/runtime", headers={"Authorization": ""})
    assert r.status_code == 401
    r = client.get("/v1/runtime")
    assert r.status_code == 200


def test_quota_heartbeat_requires_proxy_token(client: TestClient) -> None:
    r = client.post(
        "/v1/quota/heartbeat",
        json={"client_ip": "10.0.0.9", "minutes": 1},
        headers={"Authorization": ""},
    )
    assert r.status_code in (401, 404)  # 401 unauth, or 404 unknown device


def test_proxy_token_missing_config_fails_closed(tmp_path) -> None:
    """With no proxy token configured the endpoint refuses (503), never opens."""
    from seenoevil_api.app import create_app
    from seenoevil_api.config import AppConfig, DBConfig

    db_url = f"sqlite:///{tmp_path / 'no-token.db'}"
    app = create_app(AppConfig(db=DBConfig(url=db_url)))
    with TestClient(app) as c:
        r = c.post("/v1/decide", json={"url": "https://example.com/"})
        assert r.status_code == 503


# ---------------------------------------------------------------------------
# Scanner token auth on /v1/devices/discover
# ---------------------------------------------------------------------------


def test_discover_accepts_scanner_token(client: TestClient) -> None:
    r = client.post(
        "/v1/devices/discover",
        json={"devices": [{"mac": "aa:bb:cc:dd:ee:99", "ip": "10.0.0.42"}]},
        headers={"Authorization": f"Bearer {SCANNER_TOKEN}", "Cookie": ""},
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"][0]["mac"] == "aa:bb:cc:dd:ee:99"


def test_discover_rejects_wrong_token(client: TestClient) -> None:
    r = client.post(
        "/v1/devices/discover",
        json={"devices": []},
        headers={"Authorization": "Bearer nope", "Cookie": ""},
    )
    assert r.status_code == 401


def test_discover_rejects_anonymous(client: TestClient) -> None:
    r = client.post(
        "/v1/devices/discover",
        json={"devices": []},
        headers={"Authorization": "", "Cookie": ""},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Login / setup rate limiting
# ---------------------------------------------------------------------------


def test_login_rate_limited(client: TestClient) -> None:
    for _ in range(10):
        r = client.post(
            "/v1/auth/login",
            json={"email": "a@b.c", "password": "wrong"},
            headers={"Authorization": ""},
        )
        assert r.status_code == 401
    r = client.post(
        "/v1/auth/login",
        json={"email": "a@b.c", "password": "wrong"},
        headers={"Authorization": ""},
    )
    assert r.status_code == 429


def test_setup_rate_limited(client: TestClient) -> None:
    for _ in range(5):
        r = client.post(
            "/v1/auth/setup",
            json={"email": "x@y.z", "password": "correct-horse"},
            headers={"Authorization": ""},
        )
        assert r.status_code in (200, 409)  # first creates admin, rest conflict
    r = client.post(
        "/v1/auth/setup",
        json={"email": "x@y.z", "password": "correct-horse"},
        headers={"Authorization": ""},
    )
    assert r.status_code == 429


# ---------------------------------------------------------------------------
# Audit HMAC signatures
# ---------------------------------------------------------------------------


def test_audit_signature_valid_and_tamper_detected(
    admin_client: TestClient, db_session: Session
) -> None:
    admin_client.post(
        "/v1/decide",
        json={
            "url": "https://example.com/page",
            "content_type": "text/html",
            "client_ip": "10.0.0.5",
        },
    )
    rows = admin_client.get("/v1/audit").json()
    assert len(rows) == 1
    assert rows[0]["signature_valid"] is True

    # Tamper with the row behind the API's back (e.g. someone scrubbing
    # history directly in the SQLite file).
    row = db_session.scalars(select(AuditDecision)).first()
    row.url = "https://scrubbed.example.com"
    db_session.commit()

    rows = admin_client.get("/v1/audit").json()
    assert len(rows) == 1
    assert rows[0]["signature_valid"] is False


def test_legacy_unsigned_row_reports_none(admin_client: TestClient, db_session: Session) -> None:
    """Rows predating the signature column must not crash or verify."""
    profile_id = db_session.scalars(select(Profile).limit(1)).first().id
    db_session.add(
        AuditDecision(
            profile_id=profile_id,
            url="https://legacy.example.com",
            decision="allow",
            reason="default",
            classifier_scores={},
            signature=None,
        )
    )
    db_session.commit()
    rows = admin_client.get("/v1/audit").json()
    assert len(rows) == 1
    assert rows[0]["signature_valid"] is None


# ---------------------------------------------------------------------------
# Retention cleanup
# ---------------------------------------------------------------------------


def test_cleanup_expired_purges_old_rows(client: TestClient, db_session: Session) -> None:
    profile_id = db_session.scalars(select(Profile).limit(1)).first().id
    old = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=60)
    db_session.add(
        AuditDecision(
            profile_id=profile_id,
            url="https://old.example.com",
            decision="allow",
            reason="default",
            classifier_scores={},
            ts=old,
        )
    )
    db_session.add(
        AuditDecision(
            profile_id=profile_id,
            url="https://fresh.example.com",
            decision="allow",
            reason="default",
            classifier_scores={},
        )
    )
    db_session.add(
        QuarantineItem(
            profile_id=profile_id,
            url="https://oldq.example.com",
            reason="classifier:image:porn",
            classifier_scores={},
            status="denied",
            resolved_at=old,
        )
    )
    db_session.add(
        QuarantineItem(
            profile_id=profile_id,
            url="https://pending.example.com",
            reason="classifier:image:porn",
            classifier_scores={},
            status="pending",
        )
    )
    db_session.commit()

    result = cleanup_expired(db_session, retention_days=30)
    assert result == {"audit": 1, "quarantine": 1}
    assert db_session.scalars(select(AuditDecision)).all().__len__() == 1  # fresh kept
    pending = db_session.scalars(
        select(QuarantineItem).where(QuarantineItem.status == "pending")
    ).all()
    assert len(pending) == 1  # pending items survive regardless of age


# ---------------------------------------------------------------------------
# IP-based device attribution (identity spoofing fix)
# ---------------------------------------------------------------------------


def test_decide_resolves_device_by_client_ip(client: TestClient, db_session: Session) -> None:
    guests = db_session.scalars(select(Profile).where(Profile.name == "guests")).first()
    db_session.add(
        Device(mac="aa:bb:cc:dd:ee:01", name="ipad", profile_id=guests.id, ip="10.0.0.77")
    )
    db_session.commit()

    r = client.post(
        "/v1/decide",
        json={"url": "https://example.com/", "content_type": "text/html", "client_ip": "10.0.0.77"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["device_id"] is not None
    dev = db_session.get(Device, body["device_id"])
    assert dev.ip == "10.0.0.77"


def test_synthetic_mac_from_ip() -> None:
    assert _synthetic_mac_from_ip("192.168.1.10") == "02:00:c0:a8:01:0a"
    assert _synthetic_mac_from_ip("not-an-ip") is None


# ---------------------------------------------------------------------------
# Alerts webhook token
# ---------------------------------------------------------------------------


def test_alerts_webhook_optional_token(client: TestClient, monkeypatch) -> None:
    monkeypatch.setenv("SEENOEVIL_ALERTS_TOKEN", "alert-secret")
    r = client.post(
        "/v1/alerts/webhook",
        json={"alerts": [{"status": "firing", "labels": {"alertname": "X"}}]},
        headers={"Authorization": "Bearer alert-secret"},
    )
    assert r.status_code == 204
    r = client.post(
        "/v1/alerts/webhook",
        json={"alerts": []},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401
