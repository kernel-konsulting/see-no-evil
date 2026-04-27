"""SQLAlchemy engine + session helpers.

Supports SQLite (default) and PostgreSQL via the same URL form used by
SQLAlchemy and Alembic. Connection-pool tuning is ignored for SQLite.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .config import AppConfig

# Re-exported for convenience.
__all__ = ["build_engine", "make_session_factory", "session_scope", "get_db"]


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite:")


def build_engine(config: AppConfig) -> Engine:
    """Construct a SQLAlchemy engine from ``AppConfig.db``."""
    url = config.db.url
    kwargs: dict[str, Any] = {"future": True}

    if _is_sqlite(url):
        # ``check_same_thread=False`` lets FastAPI's threadpool share the
        # connection. We still use a single connection per request.
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = config.db.pool_size
        kwargs["max_overflow"] = config.db.max_overflow
        kwargs["pool_pre_ping"] = True

    engine = create_engine(url, **kwargs)

    if _is_sqlite(url):
        # Enforce foreign keys on SQLite (off by default).
        @event.listens_for(engine, "connect")
        def _fk_pragma_on_connect(dbapi_conn, _record):  # pragma: no cover - trivial
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Context-manager-style generator yielding a transactional session."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db(session_factory: sessionmaker[Session]):
    """Build a FastAPI dependency that yields a request-scoped session."""

    def _dep() -> Iterator[Session]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    return _dep
