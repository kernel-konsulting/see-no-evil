"""Whole-codebase round-3 regression tests for P0/P1/P2 fixes."""

from __future__ import annotations

import base64
import importlib.util as _ilu3
import tarfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from seenoevil_api.app import create_app
from seenoevil_api.config import (
    AllowDeny,
    AppConfig,
    AuthConfig,
    DBConfig,
    DevicesConfig,
    OIDCConfig,
    ProfileConfig,
)

_spec3 = _ilu3.spec_from_file_location("_csrf3", Path(__file__).parent / "_csrf.py")
_mod3 = _ilu3.module_from_spec(_spec3)  # type: ignore[arg-type]
assert _spec3 and _spec3.loader
_spec3.loader.exec_module(_mod3)  # type: ignore[union-attr]
inject_csrf = _mod3.inject_csrf  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# OIDC fixtures (local copy to avoid cross-file import)
# ---------------------------------------------------------------------------


@pytest.fixture
def oidc_test_config(tmp_db_url: str) -> AppConfig:
    return AppConfig(
        db=DBConfig(url=tmp_db_url),
        auth=AuthConfig(
            oidc=OIDCConfig(
                enabled=True,
                issuer="https://issuer.test",
                client_id="cid",
                client_secret="csec",
                redirect_url="https://seenoevil.lan/v1/auth/oidc/callback",
                allowed_emails=["admin@example.local"],
            )
        ),
        profiles=[
            ProfileConfig(name="kids", allow=AllowDeny(), deny=AllowDeny()),
            ProfileConfig(name="guests", allow=AllowDeny(), deny=AllowDeny()),
        ],
        devices=DevicesConfig(default_profile="guests"),
    )


class _CsrfTestClient(TestClient):
    def request(self, method: str, url: str, **kwargs):
        headers = kwargs.get("headers") or {}
        kwargs["headers"] = headers
        inject_csrf(headers, self.cookies.jar)  # type: ignore[attr-defined]
        return super().request(method, url, **kwargs)


@pytest.fixture
def oidc_client(oidc_test_config: AppConfig):
    app = create_app(oidc_test_config)
    with _CsrfTestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _admin_client_with_csrf(base_config: AppConfig) -> TestClient:
    """Return an admin-logged-in client without auto CSRF header (raw)."""
    app = create_app(base_config)
    c = TestClient(app)
    # setup + login via raw client (no auto CSRF) — need to manually handle CSRF afterwards
    # Use TestClient directly for setup/login (exempt from CSRF anyway)
    r = c.post("/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"})
    assert r.status_code == 200, r.text
    r = c.post("/v1/auth/login", json={"email": "admin@example.local", "password": "hunter22!"})
    assert r.status_code == 200, r.text
    return c


# ---------------------------------------------------------------------------
# CSRF Bearer bypass closed
# ---------------------------------------------------------------------------


def test_csrf_bearer_with_session_still_requires_token(base_config: AppConfig) -> None:
    """Authorization: Bearer x with valid session cookie must NOT bypass CSRF."""
    app = create_app(base_config)
    # Use raw client without auto header
    with TestClient(app) as c:
        c.post("/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"})
        r = c.post("/v1/auth/login", json={"email": "admin@example.local", "password": "hunter22!"})
        assert r.status_code == 200
        # Extract CSRF cookie value for later
        csrf = None
        for ck in c.cookies.jar:
            if ck.name == "seenoevil_csrf":
                csrf = ck.value
                break
        assert csrf

        # Correct: with CSRF header + Bearer + session should succeed (Bearer ignored, CSRF present)
        r = c.post(
            "/v1/profiles",
            json={"name": "with-csrf"},
            headers={"Authorization": "Bearer nonsense", "x-csrf-token": csrf},
        )
        assert r.status_code == 201, r.text

        # Without CSRF header but with Bearer + session -> must be 403 (previous bug was 201)
        r = c.post(
            "/v1/profiles",
            json={"name": "without-csrf"},
            headers={"Authorization": "Bearer nonsense"},
        )
        assert r.status_code == 403
        assert "csrf" in r.text.lower()


def test_csrf_bearer_without_session_is_exempt(base_config: AppConfig) -> None:
    """Proxy-style Bearer without session cookie is still exempt (machine-to-machine)."""
    app = create_app(base_config)
    with TestClient(app) as c:
        # No login, just Bearer -> should reach auth guard (401) not CSRF 403
        r = c.post(
            "/v1/decide",
            json={"url": "https://example.com/", "content_type": "text/html"},
            headers={"Authorization": "Bearer test-proxy-token"},
        )
        # /v1/decide requires proxy token, which test-proxy-token is, so 200
        assert r.status_code == 200


def test_csrf_login_exempt_without_token(base_config: AppConfig) -> None:
    app = create_app(base_config)
    with TestClient(app) as c:
        # login/setup are exempt from CSRF even without cookie/header
        r = c.post("/v1/auth/setup", json={"email": "a@b.c", "password": "hunter22!"})
        assert r.status_code in (200, 409)
        r = c.post("/v1/auth/login", json={"email": "a@b.c", "password": "wrong"})
        assert r.status_code == 401  # not 403


def test_csrf_disable_env_disables_protection(monkeypatch, base_config: AppConfig) -> None:
    monkeypatch.setenv("SEENOEVIL_DISABLE_CSRF", "1")
    app = create_app(base_config)
    with TestClient(app) as c:
        c.post("/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"})
        c.post("/v1/auth/login", json={"email": "admin@example.local", "password": "hunter22!"})
        # Without CSRF header, with disable flag, should not be 403
        r = c.post("/v1/profiles", json={"name": "no-csrf-disabled"}, headers={"Authorization": ""})
        # Might be 201 or 400 (profile exists) but not 403
        assert r.status_code != 403


# ---------------------------------------------------------------------------
# XFF trust and rate limiting
# ---------------------------------------------------------------------------


def test_xff_not_trusted_from_lan_publishes_rate_limit(client: TestClient) -> None:
    """192.168.x XFF spoof must not bypass rate limit."""  # noqa: E501
    # Direct LAN client 192.168.1.50 should NOT be trusted, XFF ignored.
    # Verify that spoofed XFF is still rate-limited on real IP.
    # First, exhaust limiter from 192.168.1.50's real IP without XFF
    # Use client with custom host? TestClient defaults to testclient. Simulate via XFF but not trusted.  # noqa: E501
    # Instead unit-test _host_is_trusted_proxy directly.
    import os

    from seenoevil_api.auth import _host_is_trusted_proxy

    # LAN client should not be trusted
    assert _host_is_trusted_proxy("192.168.1.50") is False
    assert _host_is_trusted_proxy("10.0.0.5") is False
    assert _host_is_trusted_proxy("10.0.5.1") is False
    # Podman/Docker container nets are trusted
    assert _host_is_trusted_proxy("10.88.0.23") is True
    assert _host_is_trusted_proxy("172.18.0.2") is True
    assert _host_is_trusted_proxy("172.16.5.4") is True
    assert _host_is_trusted_proxy("127.0.0.1") is True

    # Env override with proper CIDR should work
    os.environ["SEENOEVIL_TRUSTED_PROXIES"] = "192.168.1.0/24"
    try:
        assert _host_is_trusted_proxy("192.168.1.50") is True
        assert _host_is_trusted_proxy("192.168.2.50") is False
        # /16 truncation bug would have trusted 192.168.2.50 as well (prefix 192.168.0.)
        assert _host_is_trusted_proxy("192.168.2.50") is False
        # /16 should trust 172.18.1.5 but not outside — use non-default to avoid 172.16/12 masking  # noqa: E501
        os.environ["SEENOEVIL_TRUSTED_PROXIES"] = "203.0.113.0/24"
        assert _host_is_trusted_proxy("203.0.113.5") is True
        assert _host_is_trusted_proxy("203.0.114.5") is False
        # Exact IP
        os.environ["SEENOEVIL_TRUSTED_PROXIES"] = "10.0.0.5"
        assert _host_is_trusted_proxy("10.0.0.5") is True
        assert _host_is_trusted_proxy("10.0.0.6") is False
    finally:
        os.environ.pop("SEENOEVIL_TRUSTED_PROXIES", None)


def test_client_ip_uses_last_xff_value(base_config: AppConfig) -> None:
    """XFF with multiple values should use last (real) value, not first (spoofed)."""
    from fastapi import Request
    from seenoevil_api.auth import client_ip

    create_app(base_config)

    # Simulate trusted proxy (172.18.0.2) sending XFF "spoofed, real"
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/auth/login",
        "headers": [[b"x-forwarded-for", b"1.2.3.4, 5.6.7.8"]],
        "client": ("172.18.0.2", 12345),
    }
    req = Request(scope)
    ip = client_ip(req)
    assert ip == "5.6.7.8"  # last, not first


def test_secure_cookies_via_forwarded_proto(base_config: AppConfig, monkeypatch) -> None:
    from fastapi import Request
    from seenoevil_api.auth import _secure_cookies

    # Behind Caddy, scheme is http but X-Forwarded-Proto https should make Secure true
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [[b"x-forwarded-proto", b"https"]],
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    req = Request(scope)
    assert _secure_cookies(req) is True

    # Without header and http scheme, not secure (unless env forces)
    # Use SNE_SECURE_COOKIES=0 to force insecure in dev
    monkeypatch.setenv("SNE_SECURE_COOKIES", "0")
    scope2 = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "scheme": "http",
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    req2 = Request(scope2)
    assert _secure_cookies(req2) is False
    monkeypatch.delenv("SNE_SECURE_COOKIES", raising=False)
    # With SNE_SECURE_COOKIES=1 should be secure even on http
    monkeypatch.setenv("SNE_SECURE_COOKIES", "1")
    assert _secure_cookies(req2) is True
    monkeypatch.delenv("SNE_SECURE_COOKIES", raising=False)
    # No header, http, no env -> not secure for http, secure for https via direct scheme  # noqa: E501
    # For https we test via env unset but url scheme https is secure via direct check  # noqa: E501
    # We test via SNE_SECURE_COOKIES env path already covered


# ---------------------------------------------------------------------------
# Backup hardening
# ---------------------------------------------------------------------------


def test_backup_rejects_fifo_and_device(tmp_path: Path) -> None:
    from seenoevil_api.backup import restore
    from seenoevil_api.config import (
        AllowDeny,
        AppConfig,
        BackupConfig,
        DBConfig,
        DevicesConfig,
        PodConfig,
        ProfileConfig,
    )

    data = tmp_path / "data"
    data.mkdir()
    (data / "policy.db").write_bytes(b"fake")
    cfg = AppConfig(
        pod=PodConfig(data_dir=str(data)),
        db=DBConfig(url=f"sqlite:///{data / 'policy.db'}"),
        backup=BackupConfig(local_path=str(tmp_path / "backups")),
        profiles=[ProfileConfig(name="guests", allow=AllowDeny(), deny=AllowDeny())],
        devices=DevicesConfig(default_profile="guests"),
    )
    bad = tmp_path / "bad2.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        # FIFO
        info = tarfile.TarInfo(name="policy.db")
        info.type = tarfile.FIFOTYPE
        info.size = 0
        tar.addfile(info)
    with pytest.raises(ValueError, match="device/fifo"):
        restore(cfg, bad)

    # Symlink still rejected
    bad2 = tmp_path / "bad3.tar.gz"
    with tarfile.open(bad2, "w:gz") as tar:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        info.size = 0
        tar.addfile(info)
    with pytest.raises(ValueError, match="symlink"):
        restore(cfg, bad2)


# ---------------------------------------------------------------------------
# Audit HMAC thumbnail
# ---------------------------------------------------------------------------


def test_audit_thumbnail_hash_detects_swap(admin_client: TestClient) -> None:
    import seenoevil_api.audit_sig as sig
    from seenoevil_api.models import AuditDecision, Base, Profile

    # Create an audit row directly and verify thumbnail swap detection
    # Use admin_client's db via API: create a decide that produces audit with thumbnail?
    # Simpler: unit test canonical hash
    # We test that two rows differing only in thumbnail_b64 have different canonical and signature
    # Create in-memory DB for this unit test
    from sqlalchemy import create_engine as ce
    from sqlalchemy.orm import sessionmaker

    engine = ce("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    prof = Profile(name="guests")
    s.add(prof)
    s.commit()
    row1 = AuditDecision(
        profile_id=prof.id,
        url="https://a.com",
        decision="block",
        reason="test",
        classifier_scores={},
        thumbnail_b64="AAA=",
    )
    row2 = AuditDecision(
        profile_id=prof.id,
        url="https://a.com",
        decision="block",
        reason="test",
        classifier_scores={},
        thumbnail_b64="BBB=",
    )
    # Assign IDs manually
    row1.id = 1
    row1.ts = row2.ts = None
    row1.device_id = row2.device_id = None
    row1.profile_id = row2.profile_id = prof.id
    secret = sig.ensure_secret(s)
    # Sign row1, verify row1 ok, then swap thumbnail and verify fails
    sig.sign_row(s, row1)
    assert sig.verify_row(secret, row1) is True
    row1.thumbnail_b64 = "BBB"
    assert sig.verify_row(secret, row1) is False
    # Legacy row (old presence-bit) should still verify via fallback if signed with old logic
    # Simulate old signature with presence-bit canonical
    old_canonical = sig._canonical_legacy(secret, row1)
    import hashlib
    import hmac as hm

    old_sig = hm.new(secret.encode(), old_canonical.encode(), hashlib.sha256).hexdigest()
    row1.signature = old_sig
    # verify_row should still pass via legacy fallback
    assert sig.verify_row(secret, row1) is True


# ---------------------------------------------------------------------------
# OIDC state and nonce
# ---------------------------------------------------------------------------


def _patch_httpx_for_oidc_local(monkeypatch: pytest.MonkeyPatch, *, email: str):
    from typing import Any

    class FakeResp:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._payload

    class FakeClient:
        def __init__(self, *_a: Any, **_kw: Any) -> None:
            pass

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_a: Any) -> None:
            pass

        def close(self) -> None:
            return None

        def get(self, url: str, **kwargs: Any) -> FakeResp:
            if url.endswith("/.well-known/openid-configuration"):
                return FakeResp(
                    {
                        "issuer": "https://issuer.test",
                        "authorization_endpoint": "https://issuer.test/authorize",
                        "token_endpoint": "https://issuer.test/token",
                        "userinfo_endpoint": "https://issuer.test/userinfo",
                    }
                )
            if "userinfo" in url:
                return FakeResp({"email": email, "sub": "abc"})
            raise AssertionError(f"unexpected GET {url}")

        def post(self, url: str, **kwargs: Any) -> FakeResp:
            if "token" in url:
                return FakeResp({"access_token": "at", "id_token": "it"})
            raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr("seenoevil_api.oidc.httpx.Client", FakeClient)


def test_oidc_state_missing_cookie_rejected(
    oidc_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_for_oidc_local(monkeypatch, email="admin@example.local")
    started = oidc_client.get("/v1/auth/oidc/start").json()
    state = started["state"]
    # Delete cookie to simulate missing cookie (attacker tricking victim)
    oidc_client.cookies.clear()
    r = oidc_client.get(
        "/v1/auth/oidc/callback", params={"code": "abc", "state": state}, follow_redirects=False
    )
    assert r.status_code == 400
    assert "state mismatch" in r.text.lower() or "state" in r.text.lower()


def test_oidc_nonce_present_in_start(monkeypatch, tmp_db_url) -> None:
    from seenoevil_api import oidc as oidc_mod
    from seenoevil_api.config import (
        AllowDeny,
        AppConfig,
        AuthConfig,
        DBConfig,
        DevicesConfig,
        OIDCConfig,
        ProfileConfig,
    )
    from seenoevil_api.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    cfg = OIDCConfig(
        enabled=True,
        issuer="https://issuer.test",
        client_id="cid",
        client_secret="csec",
        redirect_url="https://seenoevil.lan/callback",
        allowed_emails=[],
    )
    AppConfig(
        db=DBConfig(url=tmp_db_url),
        auth=AuthConfig(oidc=cfg),
        profiles=[ProfileConfig(name="kids", allow=AllowDeny(), deny=AllowDeny())],
        devices=DevicesConfig(default_profile="kids"),
    )
    engine = create_engine(tmp_db_url)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()

    # Stub discover to avoid network
    monkeypatch.setattr(
        oidc_mod,
        "discover",
        lambda *a, **kw: {
            "authorization_endpoint": "https://issuer.test/authorize",
            "token_endpoint": "https://issuer.test/token",
            "userinfo_endpoint": "https://issuer.test/userinfo",
            "issuer": "https://issuer.test",
        },
    )
    started = oidc_mod.start_flow(cfg, s, redirect_url="https://seenoevil.lan/callback")
    assert "nonce=" in started.authorize_url
    # Check stored nonce
    from seenoevil_api.oidc import _STATE_PREFIX

    saved = s.get(
        __import__("seenoevil_api.models", fromlist=["Setting"]).Setting,
        _STATE_PREFIX + started.state,
    )
    assert saved.value["nonce"] is not None


def test_oidc_nonce_mismatch_rejected(monkeypatch, tmp_db_url) -> None:
    import json as _json

    from seenoevil_api import oidc as oidc_mod
    from seenoevil_api.config import (
        OIDCConfig,
    )
    from seenoevil_api.models import Base
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    cfg = OIDCConfig(
        enabled=True,
        issuer="https://issuer.test",
        client_id="cid",
        client_secret="csec",
        redirect_url="https://seenoevil.lan/callback",
        allowed_emails=[],
    )
    engine = create_engine(tmp_db_url)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    monkeypatch.setattr(
        oidc_mod,
        "discover",
        lambda *a, **kw: {
            "authorization_endpoint": "https://issuer.test/authorize",
            "token_endpoint": "https://issuer.test/token",
            "userinfo_endpoint": "https://issuer.test/userinfo",
            "issuer": "https://issuer.test",
        },
    )

    started = oidc_mod.start_flow(cfg, s, redirect_url="https://seenoevil.lan/callback")
    state = started.state

    # Fake token response with id_token containing wrong nonce
    wrong_nonce_payload = (
        base64.urlsafe_b64encode(_json.dumps({"nonce": "wrong"}).encode()).decode().rstrip("=")
    )
    fake_id = f"header.{wrong_nonce_payload}.sig"

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def close(self):
            pass

        def post(self, url, **kw):
            class R:  # noqa
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"access_token": "at", "id_token": fake_id}

            return R()

        def get(self, url, **kw):
            class R:  # noqa
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"email": "admin@example.local"}

            return R()

    monkeypatch.setattr(oidc_mod.httpx, "Client", FakeClient)
    with pytest.raises(ValueError, match="nonce mismatch"):
        oidc_mod.finish_flow(cfg, s, code="code", state=state, client=FakeClient())


# ---------------------------------------------------------------------------
# Config ImageThresholds sync
# ---------------------------------------------------------------------------


def test_image_thresholds_sync_drawing_alias() -> None:
    from seenoevil_api.config import ImageThresholds

    t = ImageThresholds(drawing=0.5)
    assert t.drawings == 0.5
    t2 = ImageThresholds(drawings=0.9)
    assert t2.drawing == 0.9
    t3 = ImageThresholds(drawing=0.4, drawings=0.8)
    assert t3.drawing == 0.8 and t3.drawings == 0.8


# ---------------------------------------------------------------------------
# Scanner fallback and lock
# ---------------------------------------------------------------------------


def test_scanner_host_network_fallback_urlparse(monkeypatch) -> None:
    pytest.importorskip("seenoevil_scanner")
    from seenoevil_scanner.scanner import load_config

    monkeypatch.setenv("API_BASE", "http://api:8000/")
    monkeypatch.setenv("SCANNER_HOST_NETWORK", "1")
    monkeypatch.setenv("API_BASE_HOST", "http://127.0.0.1:9000")
    cfg = load_config()
    assert cfg.api_base == "http://127.0.0.1:9000"

    monkeypatch.setenv("API_BASE", "http://api:8000")
    cfg2 = load_config()
    assert cfg2.api_base == "http://127.0.0.1:9000"

    # Non-api host should not fallback
    monkeypatch.setenv("API_BASE", "http://example.com:8000")
    cfg3 = load_config()
    assert cfg3.api_base == "http://example.com:8000"
