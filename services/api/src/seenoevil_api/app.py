"""FastAPI application factory + lifespan.

The lifespan does three things in order:

1. Builds the SQLAlchemy engine from the loaded ``AppConfig``.
2. Runs Alembic ``upgrade head`` so the schema is current.
3. Seeds any ``profiles[]`` from the config that do not already exist in DB.
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_admin_factory, set_admin_password
from .config import AppConfig, ProfileConfig, load_config
from .db import build_engine, get_db, make_session_factory
from .migrations import upgrade_to_head
from .models import Profile
from .routers import audit, auth, decide, devices, health, profiles

log = logging.getLogger("seenoevil_api")

_INITIAL_ADMIN_PASSWORD_ENV = "SEENOEVIL_INITIAL_ADMIN_PASSWORD"


def _profile_to_orm(p: ProfileConfig) -> Profile:
    return Profile(
        name=p.name,
        description=p.description,
        image_thresholds=dict(p.image_thresholds),
        schedule=dict(p.schedule),
        quota_minutes_per_day=p.quota_minutes_per_day,
        allow_domains=list(p.allow.domains),
        deny_domains=list(p.deny.domains),
        allow_youtube_channels=list(p.allow.youtube_channels),
        deny_youtube_channels=list(p.deny.youtube_channels),
        notify_on_block=p.notify_on_block,
    )


def seed_profiles(session: Session, config: AppConfig) -> None:
    """Insert any config profiles that are not yet in DB. Existing rows are not overwritten."""
    if not config.profiles:
        return
    existing = {n for (n,) in session.execute(select(Profile.name)).all()}
    for p in config.profiles:
        if p.name in existing:
            continue
        session.add(_profile_to_orm(p))
    session.commit()


def maybe_seed_admin(session: Session, config: AppConfig) -> None:
    """If ``SEENOEVIL_INITIAL_ADMIN_PASSWORD`` is set and no admin exists, create one."""
    pw = os.environ.get(_INITIAL_ADMIN_PASSWORD_ENV)
    if not pw:
        return
    from .auth import admin_is_configured

    if admin_is_configured(session):
        return
    try:
        set_admin_password(session, config.auth.builtin.admin_email, pw)
        session.commit()
        log.info("seeded initial admin from environment")
    except ValueError as exc:
        log.warning("ignoring initial admin env var: %s", exc)


def create_app(config: AppConfig | None = None) -> FastAPI:
    """Build the FastAPI application.

    ``config`` may be passed directly (tests) or loaded from disk via the
    ``SEENOEVIL_CONFIG`` env var (production).
    """
    if config is None:
        config = load_config()

    engine = build_engine(config)
    session_factory = make_session_factory(engine)
    db_dep = get_db(session_factory)

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Schema first (idempotent).
        try:
            upgrade_to_head(config.db.url)
        except Exception:  # pragma: no cover - surfaced via logs
            log.exception("alembic upgrade failed")
            raise
        # Then seed.
        with session_factory() as session:
            seed_profiles(session, config)
            maybe_seed_admin(session, config)
        yield
        engine.dispose()

    app = FastAPI(
        title="see-no-evil API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.engine = engine
    app.state.session_factory = session_factory

    require_admin = require_admin_factory(db_dep)

    def get_config_dep() -> AppConfig:
        return config

    app.include_router(health.make_router(db_dep))
    app.include_router(auth.make_router(db_dep))
    app.include_router(profiles.make_router(db_dep, require_admin))
    app.include_router(devices.make_router(db_dep, require_admin))
    app.include_router(audit.make_router(db_dep))
    app.include_router(decide.make_router(db_dep, get_config_dep))

    @app.exception_handler(ValueError)
    async def _value_error_handler(_request, exc: ValueError):  # pragma: no cover - trivial
        raise HTTPException(status_code=400, detail=str(exc))

    return app


def app_factory() -> FastAPI:
    """ASGI factory for ``uvicorn --factory seenoevil_api.app:app_factory``.

    Importing this module is cheap; the FastAPI app (and its DB engine) is only
    constructed when this factory is called. Tests should call ``create_app()``
    directly with an in-memory config.
    """
    return create_app()
