"""Tests for the MITM root CA distribution endpoints."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from seenoevil_api.app import create_app
from seenoevil_api.config import AppConfig


@pytest.fixture
def ca_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    cert = tmp_path / "ca.crt"
    cert.write_bytes(b"-----BEGIN CERTIFICATE-----\nFAKE\n-----END CERTIFICATE-----\n")
    monkeypatch.setenv("SEENOEVIL_MITM_CA_PATH", str(cert))
    return cert


def test_ca_info_present(base_config: AppConfig, ca_file: Path) -> None:
    with TestClient(create_app(base_config)) as c:
        r = c.get("/v1/ca/info")
        assert r.status_code == 200
        body = r.json()
        assert body["present"] is True
        assert body["path"] == str(ca_file)
        assert body["size_bytes"] == ca_file.stat().st_size
        assert "macos" in body["install"]
        assert isinstance(body["install"]["macos"], list)
        assert body["proxy_setup"]["summary"]


def test_ca_cert_download(base_config: AppConfig, ca_file: Path) -> None:
    with TestClient(create_app(base_config)) as c:
        r = c.get("/v1/ca/cert")
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/x-x509-ca-cert"
        assert "seenoevil-ca.crt" in r.headers["content-disposition"]
        assert r.content == ca_file.read_bytes()


def test_ca_cert_missing(
    base_config: AppConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("SEENOEVIL_MITM_CA_PATH", str(tmp_path / "does-not-exist.crt"))
    with TestClient(create_app(base_config)) as c:
        r = c.get("/v1/ca/cert")
        assert r.status_code == 404
        r = c.get("/v1/ca/info")
        assert r.status_code == 200
        assert r.json()["present"] is False


def test_ca_endpoints_unauthenticated(base_config: AppConfig, ca_file: Path) -> None:
    """Cert + info must work without an auth cookie (devices need it pre-trust)."""
    with TestClient(create_app(base_config)) as c:
        # No setup, no login.
        assert c.get("/v1/ca/cert").status_code == 200
        assert c.get("/v1/ca/info").status_code == 200


# Ensure env doesn't leak between sessions when tests run individually.
def teardown_module() -> None:
    os.environ.pop("SEENOEVIL_MITM_CA_PATH", None)
