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
    """Run Alembic ``upgrade head`` with file lock for concurrent replicas."""
    import contextlib
    import os
    import time

    # File lock to avoid concurrent SQLite WAL DDL races (P2).
    lock_path = os.environ.get("SEENOEVIL_MIGRATE_LOCK", "/tmp/seenoevil_migrate.lock")
    for attempt in range(5):
        try:
            import fcntl  # type: ignore[import]

            with open(lock_path, "a+") as lf:
                try:
                    fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    command.upgrade(_alembic_config(database_url), "head")
                    with contextlib.suppress(Exception):
                        fcntl.flock(lf, fcntl.LOCK_UN)
                    return
                except BlockingIOError:
                    pass
        except Exception:
            pass
        # Fallback: try without lock if fcntl unavailable (e.g. Windows) or busy
        try:
            command.upgrade(_alembic_config(database_url), "head")
            return
        except Exception as exc:
            # If database is locked, backoff and retry
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                time.sleep(0.2 * (attempt + 1))
                continue
            raise
    # Last attempt without lock
    command.upgrade(_alembic_config(database_url), "head")
