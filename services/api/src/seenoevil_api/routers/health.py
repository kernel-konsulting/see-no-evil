"""Liveness, readiness, and Prometheus metrics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from sqlalchemy.orm import Session

router = APIRouter(tags=["health"])


def make_router(get_session_dep) -> APIRouter:
    r = APIRouter(tags=["health"])

    @r.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @r.get("/readyz")
    def readyz(session: Session = Depends(get_session_dep)) -> dict[str, str]:
        # Trivial DB ping: confirms the engine can hand out a working connection.
        session.execute(text("SELECT 1"))
        return {"status": "ready"}

    @r.get("/metrics")
    def metrics() -> Response:
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return r
