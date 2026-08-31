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
    r = client.delete("/v1/audit")
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
    # Auto-discovery: a Device row is created on first contact.
    assert isinstance(body["device_id"], int) and body["device_id"] > 0


def test_decide_auto_creates_device_row(admin_client: TestClient) -> None:
    mac = "aa:bb:cc:00:11:22"
    r = admin_client.post(
        "/v1/decide",
        json={"url": "https://example.com", "device_mac": mac},
    )
    assert r.status_code == 200, r.text
    devices = admin_client.get("/v1/devices").json()
    matched = [d for d in devices if d["mac"] == mac]
    assert len(matched) == 1
    assert matched[0]["name"].startswith("auto-")


def test_decide_auto_creates_device_from_ip(admin_client: TestClient) -> None:
    r = admin_client.post(
        "/v1/decide",
        json={"url": "https://example.com", "client_ip": "192.168.1.50"},
    )
    assert r.status_code == 200, r.text
    devices = admin_client.get("/v1/devices").json()
    matched = [d for d in devices if d["ip"] == "192.168.1.50"]
    assert len(matched) == 1
    assert matched[0]["mac"] == "02:00:c0:a8:01:32"
    assert matched[0]["name"] == "auto-192.168.1.50"

    # Same IP again -> no duplicate device, last_seen updated.
    r2 = admin_client.post(
        "/v1/decide",
        json={"url": "https://example.com/2", "client_ip": "192.168.1.50"},
    )
    assert r2.status_code == 200
    devices = admin_client.get("/v1/devices").json()
    assert len([d for d in devices if d["ip"] == "192.168.1.50"]) == 1


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
    assert body["reason"] == "deny_domain:tiktok.com"
    assert body["profile"] == "kids"

    # And a row landed in the audit log.
    audit = admin_client.get("/v1/audit").json()
    assert any(row["url"] == "https://www.tiktok.com/foo" for row in audit)


def test_global_allow_is_override_not_default_deny(admin_client: TestClient) -> None:
    r = admin_client.put(
        "/v1/settings",
        json={"lists": {"global_allow_domains": ["example.edu"]}},
    )
    assert r.status_code == 200, r.text

    allowed = admin_client.post(
        "/v1/decide",
        json={"url": "https://example.edu/class", "device_mac": "ff:ff:ff:ff:ff:ff"},
    ).json()
    assert allowed["decision"] == "allow"
    assert allowed["reason"] == "global_allow_domain:example.edu"

    other = admin_client.post(
        "/v1/decide",
        json={"url": "https://victoriassecret.com/", "device_mac": "ff:ff:ff:ff:ff:ff"},
    ).json()
    assert other["decision"] == "allow"


def test_global_allowlist_requires_explicit_enforcement(admin_client: TestClient) -> None:
    r = admin_client.put(
        "/v1/settings",
        json={
            "lists": {
                "global_allow_domains": ["example.edu"],
                "enforce_global_allowlist": True,
            }
        },
    )
    assert r.status_code == 200, r.text

    blocked = admin_client.post(
        "/v1/decide",
        json={"url": "https://victoriassecret.com/", "device_mac": "ff:ff:ff:ff:ff:ff"},
    ).json()
    assert blocked["decision"] == "block"
    assert blocked["reason"] == "global_not_in_allowlist:victoriassecret.com"


def test_domain_and_url_toggles_are_separate(admin_client: TestClient) -> None:
    r = admin_client.put(
        "/v1/settings",
        json={
            "inspect": {"domain": False, "url": True},
            "lists": {
                "global_deny_domains": ["blocked.example"],
                "global_deny_keywords": ["badterm"],
            },
        },
    )
    assert r.status_code == 200, r.text

    domain_allowed = admin_client.post(
        "/v1/decide",
        json={"url": "https://blocked.example/", "device_mac": "ff:ff:ff:ff:ff:ff"},
    ).json()
    assert domain_allowed["decision"] == "allow"

    keyword_blocked = admin_client.post(
        "/v1/decide",
        json={
            "url": "https://google.com/search?q=badterm",
            "device_mac": "ff:ff:ff:ff:ff:ff",
        },
    ).json()
    assert keyword_blocked["decision"] == "block"
    assert keyword_blocked["reason"] == "global_deny_keyword:badterm"

    r = admin_client.put("/v1/settings", json={"inspect": {"domain": True, "url": False}})
    assert r.status_code == 200, r.text

    domain_blocked = admin_client.post(
        "/v1/decide",
        json={"url": "https://blocked.example/", "device_mac": "ff:ff:ff:ff:ff:ff"},
    ).json()
    assert domain_blocked["decision"] == "block"
    assert domain_blocked["reason"] == "global_deny_domain:blocked.example"

    keyword_allowed = admin_client.post(
        "/v1/decide",
        json={
            "url": "https://google.com/search?q=badterm",
            "device_mac": "ff:ff:ff:ff:ff:ff",
        },
    ).json()
    assert keyword_allowed["decision"] == "allow"


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


def test_clear_audit(admin_client: TestClient) -> None:
    admin_client.post(
        "/v1/decide",
        json={"url": "https://example.com/clear-me", "device_mac": "ff:ff:ff:ff:ff:ff"},
    )
    assert admin_client.get("/v1/audit").json()

    r = admin_client.delete("/v1/audit")
    assert r.status_code == 204
    assert admin_client.get("/v1/audit").json() == []


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


def test_proxy_forced_block_reason_is_audited_and_quarantined(admin_client: TestClient) -> None:
    r = admin_client.post(
        "/v1/decide",
        json={
            "url": "https://example.com/proxy-blocked.jpg",
            "content_type": "image/jpeg",
            "device_mac": "ff:ff:ff:ff:ff:ff",
            "classifier_scores": {"image:porn": 0.99},
            "decision": "block",
            "reason": "classifier:image:porn",
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["decision"] == "block"
    # F03: body.decision is now audit-only, policy decides via classifier_scores.
    # Policy strips the "image:" prefix, so reason is "classifier:porn".
    assert r.json()["reason"] == "classifier:porn"

    audit = admin_client.get("/v1/audit").json()
    row = next(a for a in audit if a["url"] == "https://example.com/proxy-blocked.jpg")
    assert row["decision"] == "block"
    assert row["reason"] == "classifier:porn"

    items = admin_client.get("/v1/quarantine").json()
    item = next(q for q in items if q["url"] == "https://example.com/proxy-blocked.jpg")
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


def test_quarantine_bulk_allow_and_deny(admin_client: TestClient) -> None:
    _seed_image_block(admin_client, scores={"porn": 0.99})
    _seed_image_block(admin_client, scores={"porn": 0.99})

    r = admin_client.post("/v1/quarantine/bulk-allow", json={})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 2
    assert r.json().get("skipped_expired", 0) == 0
    assert admin_client.get("/v1/quarantine").json() == []

    _seed_image_block(admin_client, scores={"porn": 0.99})
    _seed_image_block(admin_client, scores={"porn": 0.99})
    pending = admin_client.get("/v1/quarantine").json()
    selected_id = pending[0]["id"]

    r = admin_client.post("/v1/quarantine/bulk-deny", json={"ids": [selected_id]})
    assert r.status_code == 200, r.text
    assert r.json()["updated"] == 1
    assert r.json().get("skipped_expired", 0) == 0
    assert len(admin_client.get("/v1/quarantine").json()) == 1


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


def test_quarantine_viewer_forbidden(client: TestClient) -> None:
    client.post("/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"})
    client.post("/v1/auth/login", json={"email": "admin@example.local", "password": "hunter22!"})
    r = client.post(
        "/v1/users",
        json={"email": "viewer@example.local", "password": "viewer22!", "role": "viewer"},
    )
    assert r.status_code == 201, r.text
    client.post("/v1/auth/logout")
    r = client.post(
        "/v1/auth/login",
        json={"email": "viewer@example.local", "password": "viewer22!"},
    )
    assert r.status_code == 200, r.text

    # Viewers can now list and flag quarantine (read + flag) but cannot mutate.
    assert client.get("/v1/quarantine").status_code == 200
    assert client.get("/v1/quarantine/1").status_code in (200, 404)
    # Flag should succeed (or 404 if no item) — never 403.
    assert client.post("/v1/quarantine/1/flag", json={"note": "maybe safe"}).status_code in (
        200,
        404,
    )
    # But allow/deny/delete still admin-only.
    assert client.post("/v1/quarantine/1/allow").status_code == 403
    assert client.post("/v1/quarantine/1/deny").status_code == 403
    assert client.delete("/v1/quarantine/1").status_code == 403


def test_quarantine_delete(admin_client: TestClient) -> None:
    _seed_image_block(admin_client, scores={"porn": 0.99})
    item_id = admin_client.get("/v1/quarantine").json()[0]["id"]
    admin_client.post(f"/v1/quarantine/{item_id}/deny")
    r = admin_client.delete(f"/v1/quarantine/{item_id}")
    assert r.status_code == 204
    assert admin_client.get(f"/v1/quarantine/{item_id}").status_code == 404
