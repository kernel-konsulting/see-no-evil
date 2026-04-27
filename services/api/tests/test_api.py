"""End-to-end-ish tests through TestClient."""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_healthz_ready_metrics(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert b"python_info" in metrics.content


def test_seeded_profiles_are_visible(admin_client: TestClient) -> None:
    r = admin_client.get("/v1/profiles")
    assert r.status_code == 200
    names = {p["name"] for p in r.json()}
    assert {"kids", "guests"} <= names


def test_control_plane_reads_require_admin(client: TestClient) -> None:
    protected_paths = (
        "/v1/profiles",
        "/v1/profiles/1",
        "/v1/devices",
        "/v1/devices/1",
        "/v1/audit",
        "/v1/quarantine",
        "/v1/quarantine/1",
    )
    for path in protected_paths:
        r = client.get(path)
        assert r.status_code == 401


def test_control_plane_writes_require_admin(client: TestClient) -> None:
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


# ---------------------------------------------------------------------------
# M3 — Quarantine queue
# ---------------------------------------------------------------------------


def _seed_image_block(admin_client: TestClient, *, scores: dict[str, float]) -> dict:
    r = admin_client.post(
        "/v1/decide",
        json={
            "url": "https://example.com/pic.jpg",
            "content_type": "image/jpeg",
            "device_mac": "ff:ff:ff:ff:ff:ff",
            "classifier_scores": scores,
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_quarantine_created_on_classifier_block(admin_client: TestClient) -> None:
    body = _seed_image_block(admin_client, scores={"porn": 0.99})
    assert body["decision"] == "block"
    assert body["reason"].startswith("classifier:")

    items = admin_client.get("/v1/quarantine").json()
    assert len(items) == 1
    item = items[0]
    assert item["url"] == "https://example.com/pic.jpg"
    assert item["status"] == "pending"
    assert item["reason"] == "classifier:porn"


def test_quarantine_not_created_for_domain_block(admin_client: TestClient) -> None:
    profiles = admin_client.get("/v1/profiles").json()
    kids_id = next(p["id"] for p in profiles if p["name"] == "kids")
    dev = admin_client.post(
        "/v1/devices", json={"mac": "aa:bb:cc:dd:ee:01", "profile_id": kids_id}
    ).json()
    r = admin_client.post(
        "/v1/decide",
        json={"url": "https://www.tiktok.com/foo", "device_id": dev["id"]},
    )
    assert r.json()["decision"] == "block"
    items = admin_client.get("/v1/quarantine").json()
    assert items == []


def test_quarantine_skips_non_image_content(admin_client: TestClient) -> None:
    r = admin_client.post(
        "/v1/decide",
        json={
            "url": "https://example.com/page.html",
            "content_type": "text/html",
            "device_mac": "ff:ff:ff:ff:ff:ff",
            "classifier_scores": {"porn": 0.99},
        },
    )
    assert r.json()["decision"] == "block"
    assert admin_client.get("/v1/quarantine").json() == []


def test_quarantine_allow_and_deny_lifecycle(admin_client: TestClient) -> None:
    _seed_image_block(admin_client, scores={"porn": 0.99})
    _seed_image_block(admin_client, scores={"porn": 0.99})

    items = admin_client.get("/v1/quarantine").json()
    assert len(items) == 2
    a, b = items[0]["id"], items[1]["id"]

    allowed = admin_client.post(f"/v1/quarantine/{a}/allow").json()
    assert allowed["status"] == "allowed"
    assert allowed["resolved_by"] == "admin"

    denied = admin_client.post(f"/v1/quarantine/{b}/deny").json()
    assert denied["status"] == "denied"

    # Pending list should now be empty.
    pending = admin_client.get("/v1/quarantine").json()
    assert pending == []

    # Resolving twice is a 409.
    again = admin_client.post(f"/v1/quarantine/{a}/allow")
    assert again.status_code == 409


def test_quarantine_filter_by_status(admin_client: TestClient) -> None:
    _seed_image_block(admin_client, scores={"porn": 0.99})
    item_id = admin_client.get("/v1/quarantine").json()[0]["id"]
    admin_client.post(f"/v1/quarantine/{item_id}/deny")

    assert admin_client.get("/v1/quarantine", params={"status": "denied"}).json()
    assert admin_client.get("/v1/quarantine", params={"status": "pending"}).json() == []
    assert admin_client.get("/v1/quarantine", params={"status": "all"}).json()


def test_quarantine_admin_only(client: TestClient) -> None:
    # Need to first generate an item via the unauthenticated decide endpoint.
    client.post(
        "/v1/decide",
        json={
            "url": "https://example.com/pic.jpg",
            "content_type": "image/jpeg",
            "classifier_scores": {"porn": 0.99},
        },
    )
    assert client.get("/v1/quarantine").status_code == 401
    assert client.get("/v1/quarantine/1").status_code == 401
    assert client.post("/v1/quarantine/1/allow").status_code == 401


def test_quarantine_delete(admin_client: TestClient) -> None:
    _seed_image_block(admin_client, scores={"porn": 0.99})
    item_id = admin_client.get("/v1/quarantine").json()[0]["id"]
    admin_client.post(f"/v1/quarantine/{item_id}/deny")
    r = admin_client.delete(f"/v1/quarantine/{item_id}")
    assert r.status_code == 204
    assert admin_client.get(f"/v1/quarantine/{item_id}").status_code == 404
