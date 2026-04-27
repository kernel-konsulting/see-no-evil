"""Tests for the first-run wizard (services/api/src/seenoevil_api/wizard.py)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from seenoevil_api.wizard import (
    UPSTREAM_PRESETS,
    _build_config,
    _seed_admin,
)

# ---------------------------------------------------------------------------
# _build_config
# ---------------------------------------------------------------------------


def test_build_config_defaults():
    cfg = _build_config(
        data_dir="/data",
        admin_email="admin@test.local",
        dns_upstream="cloudflare-family",
        hostname="seenoevil.lan",
    )
    assert cfg["pod"]["hostname"] == "seenoevil.lan"
    assert cfg["pod"]["data_dir"] == "/data"
    assert cfg["auth"]["builtin"]["admin_email"] == "admin@test.local"
    assert cfg["dns"]["upstreams"] == UPSTREAM_PRESETS["cloudflare-family"]
    assert any(p["name"] == "guests" for p in cfg["profiles"])
    assert cfg["devices"]["default_profile"] == "guests"


def test_build_config_all_upstreams():
    for key in UPSTREAM_PRESETS:
        cfg = _build_config("/data", "x@y.com", dns_upstream=key, hostname="h.lan")
        assert cfg["dns"]["upstreams"] == UPSTREAM_PRESETS[key]


def test_build_config_roundtrips_yaml(tmp_path: Path):
    cfg = _build_config("/data", "x@y.com", "cloudflare", "test.lan")
    out = tmp_path / "config.yaml"
    out.write_text(yaml.dump(cfg, default_flow_style=False, sort_keys=False))
    loaded = yaml.safe_load(out.read_text())
    assert loaded["pod"]["hostname"] == "test.lan"


# ---------------------------------------------------------------------------
# _seed_admin
# ---------------------------------------------------------------------------


def test_seed_admin_creates_account(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    _seed_admin(db_url, "admin@example.com", "supersecret123")

    from seenoevil_api.auth import admin_is_configured, verify_admin
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(db_url, future=True)
    with Session(engine) as session:
        assert admin_is_configured(session)
        assert verify_admin(session, "admin@example.com", "supersecret123")
        assert not verify_admin(session, "admin@example.com", "wrongpassword")


def test_seed_admin_idempotent(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    _seed_admin(db_url, "admin@example.com", "firstpassword1")
    # Second call — already configured; should not raise.
    _seed_admin(db_url, "admin@example.com", "secondpassword1")

    from seenoevil_api.auth import verify_admin
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    engine = create_engine(db_url, future=True)
    with Session(engine) as session:
        # Original password must still work (idempotent = no overwrite).
        assert verify_admin(session, "admin@example.com", "firstpassword1")


def test_seed_admin_rejects_short_password(tmp_path: Path):
    db_url = f"sqlite:///{tmp_path / 'test.db'}"
    with pytest.raises((ValueError, SystemExit)):
        _seed_admin(db_url, "admin@example.com", "short")


# ---------------------------------------------------------------------------
# main() — non-interactive (env-var driven)
# ---------------------------------------------------------------------------


def test_main_noninteractive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Wizard should complete without prompts when all env vars are set."""
    config_path = tmp_path / "config.yaml"

    monkeypatch.setenv("SEENOEVIL_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SEENOEVIL_INITIAL_ADMIN_EMAIL", "ci@test.local")
    monkeypatch.setenv("SEENOEVIL_INITIAL_ADMIN_PASSWORD", "ci_password_123")
    monkeypatch.setenv("SNE_HOSTNAME", "ci.lan")
    monkeypatch.setenv("SEENOEVIL_CONFIG", str(config_path))

    # Mock interactive prompts to fail if called.
    with (
        patch("builtins.input", side_effect=AssertionError("unexpected prompt")),
        patch("getpass.getpass", side_effect=AssertionError("unexpected password prompt")),
        patch("seenoevil_api.wizard._prompt_choice", return_value="cloudflare-family"),
    ):
        from seenoevil_api.wizard import main

        main()

    assert config_path.exists()
    loaded = yaml.safe_load(config_path.read_text())
    assert loaded["pod"]["hostname"] == "ci.lan"
