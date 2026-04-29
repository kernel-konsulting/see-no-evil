"""Runtime settings — read/write the global proxy/notification config.

Two surfaces:

* ``GET/PUT /v1/settings`` — admin-authenticated. Used by the UI Settings page.
* ``GET /v1/runtime`` — unauthenticated; proxy polls this. The pod network is
  trusted so we don't sign or token-protect this; the only consumer is the
  in-pod proxy. (Bind only the loopback interface in production.)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import runtime


def make_router(get_session_dep, require_admin) -> APIRouter:
    r = APIRouter(prefix="/v1", tags=["settings"])

    @r.get("/settings")
    def get_settings(
        session: Session = Depends(get_session_dep),
        _: str = Depends(require_admin),
    ) -> dict[str, Any]:
        return runtime.get_runtime(session)

    @r.put("/settings")
    def put_settings(
        patch: dict[str, Any],
        session: Session = Depends(get_session_dep),
        _: str = Depends(require_admin),
    ) -> dict[str, Any]:
        return runtime.update_runtime(session, patch)

    @r.get("/runtime")
    def get_runtime_public(session: Session = Depends(get_session_dep)) -> dict[str, Any]:
        return runtime.get_runtime(session)

    return r
