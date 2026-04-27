"""Tests for the Pydantic config schema and YAML loader."""

from __future__ import annotations

from pathlib import Path

import pytest
from seenoevil_api.config import (
    AppConfig,
    load_config,
    normalize_mac,
    parse_duration_seconds,
    parse_size_bytes,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_example_config_validates() -> None:
    cfg = load_config(REPO_ROOT / "config.example.yaml")
    assert cfg.pod.hostname == "seenoevil.lan"
    assert any(p.name == "kids" for p in cfg.profiles)
    assert cfg.devices.default_profile == "guests"


def test_default_appconfig() -> None:
    cfg = AppConfig()
    assert cfg.db.url.startswith("sqlite:")
    assert cfg.cache.kind == "embedded"


def test_db_url_validation() -> None:
    with pytest.raises(ValueError):
        AppConfig.model_validate({"db": {"url": "mysql://nope"}})


def test_profile_uniqueness() -> None:
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {
                "profiles": [
                    {"name": "kids"},
                    {"name": "kids"},
                ]
            }
        )


def test_default_profile_must_exist() -> None:
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {
                "profiles": [{"name": "kids"}],
                "devices": {"default_profile": "missing"},
            }
        )


def test_static_device_profile_must_exist() -> None:
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {
                "profiles": [{"name": "kids"}],
                "devices": {
                    "default_profile": "kids",
                    "static": [
                        {"mac": "aa:bb:cc:dd:ee:ff", "profile": "ghost"},
                    ],
                },
            }
        )


def test_redis_requires_url() -> None:
    with pytest.raises(ValueError):
        AppConfig.model_validate({"cache": {"kind": "redis"}})


def test_normalize_mac_accepts_common_formats() -> None:
    assert normalize_mac("AA:BB:CC:DD:EE:FF") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aa-bb-cc-dd-ee-ff") == "aa:bb:cc:dd:ee:ff"
    assert normalize_mac("aabbccddeeff") == "aa:bb:cc:dd:ee:ff"


def test_normalize_mac_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        normalize_mac("not-a-mac")


def test_parse_duration() -> None:
    assert parse_duration_seconds("30s") == 30
    assert parse_duration_seconds("5m") == 300
    assert parse_duration_seconds("24h") == 86400
    assert parse_duration_seconds("7d") == 7 * 86400
    with pytest.raises(ValueError):
        parse_duration_seconds("3 fortnights")


def test_parse_size() -> None:
    assert parse_size_bytes("10MiB") == 10 * 1024**2
    assert parse_size_bytes("2GiB") == 2 * 1024**3
    with pytest.raises(ValueError):
        parse_size_bytes("big")
