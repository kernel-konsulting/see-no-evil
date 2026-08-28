"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from seenoevil_api.app import create_app
from seenoevil_api.auth import clear_rate_limiters
from seenoevil_api.config import (
    AllowDeny,
    AppConfig,
    DBConfig,
    DevicesConfig,
    ProfileConfig,
    ProxyAPIConfig,
    ScannerConfig,
)

# Shared secrets the test API instance expects from its in-pod clients.
PROXY_TOKEN = "test-proxy-token"
SCANNER_TOKEN = "test-scanner-token"


class AuthedClient(TestClient):
    """TestClient that presents the proxy bearer token on every request.

    The real proxy authenticates to /v1/decide, /v1/runtime and
    /v1/quota/heartbeat with `Authorization: Bearer <token>`; tests that
    exercise those endpoints get the header for free, and tests of the
    unauthenticated paths can pass headers={"Authorization": ""} to opt out.
    """

    def request(self, method: str, url: str, **kwargs):
        headers = kwargs.get("headers") or {}
        kwargs["headers"] = headers
        headers.setdefault("Authorization", f"Bearer {PROXY_TOKEN}")
        return super().request(method, url, **kwargs)


@pytest.fixture(autouse=True)
def _reset_rate_limiters() -> Iterator[None]:
    """Isolate the in-memory login/setup rate limiters between tests."""
    clear_rate_limiters()
    yield
    clear_rate_limiters()


@pytest.fixture
def tmp_db_url() -> Iterator[str]:
    """A throwaway file-backed SQLite URL.

    File-backed (rather than ``:memory:``) so the URL is sharable between the
    Alembic invocation and the engine handed to FastAPI.
    """
    fd, path = tempfile.mkstemp(prefix="seenoevil-test-", suffix=".db")
    os.close(fd)
    try:
        yield f"sqlite:///{path}"
    finally:
        Path(path).unlink(missing_ok=True)


@pytest.fixture
def base_config(tmp_db_url: str) -> AppConfig:
    return AppConfig(
        db=DBConfig(url=tmp_db_url),
        proxy=ProxyAPIConfig(api_token=PROXY_TOKEN),
        scanner=ScannerConfig(api_token=SCANNER_TOKEN),
        profiles=[
            ProfileConfig(
                name="kids",
                description="seeded",
                image_thresholds={"porn": 0.4},
                schedule={},
                quota_minutes_per_day=0,
                allow=AllowDeny(domains=[]),
                deny=AllowDeny(domains=["tiktok.com"]),
            ),
            ProfileConfig(
                name="guests",
                description="default",
                schedule={},
                quota_minutes_per_day=0,
                allow=AllowDeny(domains=[]),
                deny=AllowDeny(domains=[]),
            ),
        ],
        devices=DevicesConfig(default_profile="guests"),
    )


@pytest.fixture
def client(base_config: AppConfig) -> Iterator[TestClient]:
    app = create_app(base_config)
    with AuthedClient(app) as c:
        yield c


@pytest.fixture
def admin_client(base_config: AppConfig) -> Iterator[TestClient]:
    """A TestClient that has already completed admin setup + login."""
    app = create_app(base_config)
    with AuthedClient(app) as c:
        r = c.post("/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"})
        assert r.status_code == 200, r.text
        r = c.post("/v1/auth/login", json={"email": "admin@example.local", "password": "hunter22!"})
        assert r.status_code == 200, r.text
        yield c
