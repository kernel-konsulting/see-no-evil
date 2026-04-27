"""End-to-end-ish tests through TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_ready_metrics(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert b"python_info" in metrics.content


def test_seeded_profiles_are_visible(client: TestClient) -> None:
    r = client.get("/v1/profiles")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert {"kids", "guests"} <= names


def test_profile_crud_requires_admin(client: TestClient) -> None:
    r = client.post("/v1/profiles", json={"name": "blocked"})
    assert r.status_code == 401


def test_profile_crud_round_trip(admin_client: TestClient) -> None:
    create = admin_client.post(
        "/v1/profiles",
        json={"name": "teens", "deny_domains": ["tiktok.com"]},
    )
    assert create.status_code == 201, create.text
    pid = create.json()["id"]

    patch = admin_client.patch(f"/v1/profiles/{pid}", json={"description": "for teenagers"})
    assert patch.status_code == 200
    assert patch.json()["description"] == "for teenagers"

    delete = admin_client.delete(f"/v1/profiles/{pid}")
    assert delete.status_code == 204

    assert admin_client.get(f"/v1/profiles/{pid}").status_code == 404


def test_duplicate_profile_name_rejected(admin_client: TestClient) -> None:
    r = admin_client.post("/v1/profiles", json={"name": "kids"})
    assert r.status_code == 409


def test_device_crud(admin_client: TestClient) -> None:
    profiles = admin_client.get("/v1/profiles").json()
    kids_id = next(p["id"] for p in profiles if p["name"] == "kids")

    r = admin_client.post(
        "/v1/devices",
        json={"mac": "AA-BB-CC-DD-EE-FF", "name": "Kid iPad", "profile_id": kids_id},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["mac"] == "aa:bb:cc:dd:ee:ff"
    did = body["id"]

    # Duplicate MAC
    dup = admin_client.post("/v1/devices", json={"mac": "aa:bb:cc:dd:ee:ff", "profile_id": kids_id})
    assert dup.status_code == 409

    # Bad profile id
    bad = admin_client.post("/v1/devices", json={"mac": "11:22:33:44:55:66", "profile_id": 99999})
    assert bad.status_code == 400

    assert admin_client.delete(f"/v1/devices/{did}").status_code == 204


def test_decide_unknown_device_uses_default_profile(client: TestClient) -> None:
    r = client.post(
        "/v1/decide",
        json={
            "url": "https://example.com",
            "device_mac": "ff:ff:ff:ff:ff:ff",
            "classifier_scores": {},
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["decision"] == "allow"
    assert body["profile"] == "guests"
    assert body["device_id"] is None


def test_decide_known_device_blocks_on_deny(admin_client: TestClient) -> None:
    profiles = admin_client.get("/v1/profiles").json()
    kids_id = next(p["id"] for p in profiles if p["name"] == "kids")
    dev = admin_client.post(
        "/v1/devices", json={"mac": "aa:bb:cc:dd:ee:01", "profile_id": kids_id}
    ).json()

    r = admin_client.post(
        "/v1/decide",
        json={"url": "https://www.tiktok.com/foo", "device_id": dev["id"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["decision"] == "block"
    assert body["reason"] == "deny_domain"
    assert body["profile"] == "kids"

    # And a row landed in the audit log.
    audit = admin_client.get("/v1/audit").json()
    assert any(row["url"] == "https://www.tiktok.com/foo" for row in audit)


def test_audit_filters(admin_client: TestClient) -> None:
    # Generate a couple decisions
    admin_client.post(
        "/v1/decide",
        json={"url": "https://example.com/a", "device_mac": "ff:ff:ff:ff:ff:ff"},
    )
    admin_client.post(
        "/v1/decide",
        json={"url": "https://example.com/b", "device_mac": "ff:ff:ff:ff:ff:ff"},
    )
    r = admin_client.get("/v1/audit", params={"decision": "allow", "limit": 10})
    assert r.status_code == 200
    rows = r.json()
    assert all(row["decision"] == "allow" for row in rows)


def test_auth_setup_only_once(client: TestClient) -> None:
    first = client.post(
        "/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"}
    )
    assert first.status_code == 200
    second = client.post(
        "/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"}
    )
    assert second.status_code == 409


def test_login_failure(client: TestClient) -> None:
    client.post("/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"})
    r = client.post("/v1/auth/login", json={"email": "admin@example.local", "password": "wrong"})
    assert r.status_code == 401


def test_logout_clears_cookie(admin_client: TestClient) -> None:
    r = admin_client.post("/v1/auth/logout")
    assert r.status_code == 204
    # After logout, admin endpoints should refuse.
    admin_client.cookies.clear()
    r = admin_client.post("/v1/profiles", json={"name": "blocked"})
    assert r.status_code == 401
