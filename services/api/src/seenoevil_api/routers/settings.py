"""Runtime settings — read/write the global proxy/notification config.

Two surfaces:

* ``GET/PUT /v1/settings`` — admin-authenticated. Used by the UI Settings page.
* ``GET /v1/runtime`` — proxy-authenticated (Bearer token); the in-pod proxy
  polls this. Token-protected so LAN clients hitting the API through Caddy
  cannot read thresholds or global lists.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import runtime
from ..auth import require_proxy_factory


def make_router(get_session_dep, require_admin, get_config) -> APIRouter:
    r = APIRouter(prefix="/v1", tags=["settings"])
    require_proxy = require_proxy_factory(get_config)

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
    def get_runtime_public(
        session: Session = Depends(get_session_dep),
        _proxy: str = Depends(require_proxy),
    ) -> dict[str, Any]:
        return runtime.get_runtime(session)

    return r
