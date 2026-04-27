"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from seenoevil_api.app import create_app
from seenoevil_api.config import (
    AllowDeny,
    AppConfig,
    DBConfig,
    DevicesConfig,
    ProfileConfig,
)


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
    with TestClient(app) as c:
        yield c


@pytest.fixture
def admin_client(base_config: AppConfig) -> Iterator[TestClient]:
    """A TestClient that has already completed admin setup + login."""
    app = create_app(base_config)
    with TestClient(app) as c:
        r = c.post("/v1/auth/setup", json={"email": "admin@example.local", "password": "hunter22!"})
        assert r.status_code == 200, r.text
        r = c.post("/v1/auth/login", json={"email": "admin@example.local", "password": "hunter22!"})
        assert r.status_code == 200, r.text
        yield c
