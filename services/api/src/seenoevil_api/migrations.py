"""Alembic helpers callable from the FastAPI lifespan."""

from __future__ import annotations

from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config


def _alembic_config(database_url: str) -> Config:
    pkg_root = resources.files("seenoevil_api")
    ini_path = Path(str(pkg_root / "alembic.ini"))
    script_location = Path(str(pkg_root / "alembic"))
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(script_location))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_to_head(database_url: str) -> None:
    """Run Alembic ``upgrade head`` against ``database_url``."""
    command.upgrade(_alembic_config(database_url), "head")
