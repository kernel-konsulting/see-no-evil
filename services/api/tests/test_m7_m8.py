"""M7 (device enrichment) + M8 (OIDC, backup, config) tests."""

from __future__ import annotations

import importlib.util as _ilu2
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from seenoevil_api import backup as backup_mod
from seenoevil_api.app import create_app
from seenoevil_api.config import (
    AllowDeny,
    AppConfig,
    AuthConfig,
    BackupConfig,
    DBConfig,
    DevicesConfig,
    LitestreamConfig,
    OIDCConfig,
    PodConfig,
    ProfileConfig,
)

_spec2 = _ilu2.spec_from_file_location("_csrf2", Path(__file__).parent / "_csrf.py")
_mod2 = _ilu2.module_from_spec(_spec2)  # type: ignore[arg-type]
assert _spec2 and _spec2.loader
_spec2.loader.exec_module(_mod2)  # type: ignore[union-attr]
inject_csrf = _mod2.inject_csrf  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# M7 — discover stores ip + vendor; UI returns them
# ---------------------------------------------------------------------------


def test_discover_persists_ip_and_vendor(admin_client: TestClient) -> None:
    r = admin_client.post(
        "/v1/devices/discover",
        json={
            "devices": [
                {
                    "mac": "11:22:33:44:55:77",
                    "ip": "192.168.1.42",
                    "hostname": "kid-tablet",
                    "vendor": "Apple",
                }
            ]
        },
    )
    assert r.status_code == 200, r.text
    devices = admin_client.get("/v1/devices").json()
    found = next(d for d in devices if d["mac"] == "11:22:33:44:55:77")
    assert found["ip"] == "192.168.1.42"
    assert found["vendor"] == "Apple"
    assert found["name"] == "kid-tablet"


def test_discover_refreshes_ip_keeps_vendor(admin_client: TestClient) -> None:
    admin_client.post(
        "/v1/devices/discover",
        json={"devices": [{"mac": "11:22:33:44:55:88", "ip": "10.0.0.5", "vendor": "Acme"}]},
    )
    admin_client.post(
        "/v1/devices/discover",
        json={"devices": [{"mac": "11:22:33:44:55:88", "ip": "10.0.0.6", "vendor": "Other"}]},
    )
    devices = admin_client.get("/v1/devices").json()
    found = next(d for d in devices if d["mac"] == "11:22:33:44:55:88")
    # IP refreshed.
    assert found["ip"] == "10.0.0.6"
    # Vendor preserved (we don't overwrite once known).
    assert found["vendor"] == "Acme"


# ---------------------------------------------------------------------------
# M8 — OIDC config validation + flow
# ---------------------------------------------------------------------------


def test_oidc_config_requires_issuer_when_enabled() -> None:
    with pytest.raises(ValueError):
        OIDCConfig(enabled=True)


def test_oidc_config_disabled_by_default() -> None:
    cfg = OIDCConfig()
    assert cfg.enabled is False


@pytest.fixture
def oidc_config(tmp_db_url: str) -> AppConfig:
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


class _CsrfClient(TestClient):
    def request(self, method: str, url: str, **kwargs):  # type: ignore[override]
        headers = kwargs.get("headers") or {}
        kwargs["headers"] = headers
        inject_csrf(headers, self.cookies.jar)  # type: ignore[attr-defined]
        return super().request(method, url, **kwargs)


@pytest.fixture
def oidc_client(oidc_config: AppConfig) -> Iterator[TestClient]:
    app = create_app(oidc_config)
    with _CsrfClient(app) as c:
        yield c


def _patch_httpx_for_oidc(monkeypatch: pytest.MonkeyPatch, *, email: str) -> list[dict[str, Any]]:
    """Stub httpx.Client to return canned OIDC discovery + token + userinfo responses."""
    calls: list[dict[str, Any]] = []

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
            calls.append({"method": "GET", "url": url, **kwargs})
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
            calls.append({"method": "POST", "url": url, **kwargs})
            if "token" in url:
                return FakeResp({"access_token": "at", "id_token": "it"})
            raise AssertionError(f"unexpected POST {url}")

    monkeypatch.setattr("seenoevil_api.oidc.httpx.Client", FakeClient)
    return calls


def test_oidc_start_returns_authorize_url(
    oidc_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_for_oidc(monkeypatch, email="admin@example.local")
    r = oidc_client.get("/v1/auth/oidc/start")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["authorize_url"].startswith("https://issuer.test/authorize?")
    assert "code_challenge=" in body["authorize_url"]
    assert body["state"]


def test_oidc_callback_issues_session(
    oidc_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_for_oidc(monkeypatch, email="admin@example.local")
    started = oidc_client.get("/v1/auth/oidc/start").json()
    state = started["state"]

    r = oidc_client.get(
        "/v1/auth/oidc/callback",
        params={"code": "abc", "state": state},
        follow_redirects=False,
    )
    assert r.status_code == 303
    # Cookie was set; subsequent admin calls should succeed.
    create = oidc_client.post("/v1/profiles", json={"name": "via_oidc"})
    assert create.status_code == 201, create.text


def test_oidc_callback_rejects_disallowed_email(
    oidc_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_for_oidc(monkeypatch, email="stranger@example.com")
    started = oidc_client.get("/v1/auth/oidc/start").json()
    r = oidc_client.get(
        "/v1/auth/oidc/callback",
        params={"code": "abc", "state": started["state"]},
        follow_redirects=False,
    )
    assert r.status_code == 403


def test_oidc_callback_rejects_unknown_state(
    oidc_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_httpx_for_oidc(monkeypatch, email="admin@example.local")
    r = oidc_client.get(
        "/v1/auth/oidc/callback",
        params={"code": "abc", "state": "nonexistent"},
        follow_redirects=False,
    )
    assert r.status_code == 400


def test_oidc_disabled_returns_404(client: TestClient) -> None:
    # base_config has no OIDC.
    assert client.get("/v1/auth/oidc/start").status_code == 404


# ---------------------------------------------------------------------------
# M8 — backup snapshot/restore CLI
# ---------------------------------------------------------------------------


@pytest.fixture
def backup_config(tmp_path: Path) -> AppConfig:
    data = tmp_path / "data"
    backups = tmp_path / "backups"
    (data / "ca").mkdir(parents=True)
    (data / "policy.db").write_bytes(b"SQLite format 3\x00fakedb")
    (data / "ca" / "seenoevil-ca.crt").write_text("PEM")
    return AppConfig(
        pod=PodConfig(data_dir=str(data)),
        db=DBConfig(url=f"sqlite:///{data / 'policy.db'}"),
        backup=BackupConfig(local_path=str(backups), retention=2),
        profiles=[ProfileConfig(name="guests", allow=AllowDeny(), deny=AllowDeny())],
        devices=DevicesConfig(default_profile="guests"),
    )


def test_backup_snapshot_writes_archive(backup_config: AppConfig) -> None:
    out = backup_mod.snapshot(backup_config)
    assert out.exists()
    assert out.suffix == ".gz"
    assert out in backup_mod.list_snapshots(backup_config.backup)


def test_backup_retention_prunes_old(
    backup_config: AppConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force three different stamps.
    stamps = iter(["20260101T000000Z", "20260102T000000Z", "20260103T000000Z"])

    class FakeDt:
        @classmethod
        def now(cls, _tz: Any = None) -> Any:
            class _D:
                @staticmethod
                def strftime(_: str) -> str:
                    return next(stamps)

            return _D()

    monkeypatch.setattr("seenoevil_api.backup.datetime", FakeDt)

    backup_mod.snapshot(backup_config)
    backup_mod.snapshot(backup_config)
    backup_mod.snapshot(backup_config)
    snaps = backup_mod.list_snapshots(backup_config.backup)
    # Retention is 2.
    assert len(snaps) == 2
    assert snaps[0].name.endswith("20260102T000000Z.tar.gz")


def test_backup_restore_round_trip(backup_config: AppConfig, tmp_path: Path) -> None:
    out = backup_mod.snapshot(backup_config)
    # Wipe data_dir then restore.
    data = Path(backup_config.pod.data_dir)
    (data / "policy.db").unlink()
    backup_mod.restore(backup_config, out)
    assert (data / "policy.db").read_bytes().startswith(b"SQLite format 3")


def test_backup_restore_rejects_traversal(backup_config: AppConfig, tmp_path: Path) -> None:
    import tarfile

    bad = tmp_path / "bad.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escape")
        info.size = 0
        tar.addfile(info, fileobj=None)
    with pytest.raises(ValueError):
        backup_mod.restore(backup_config, bad)


# ---------------------------------------------------------------------------
# M8 — Litestream config validation
# ---------------------------------------------------------------------------


def test_litestream_requires_replica_url() -> None:
    with pytest.raises(ValueError):
        LitestreamConfig(enabled=True)


def test_litestream_disabled_default() -> None:
    assert LitestreamConfig().enabled is False
