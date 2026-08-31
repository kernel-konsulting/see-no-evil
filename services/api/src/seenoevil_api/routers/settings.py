"""Runtime settings — read/write the global proxy/notification config.

Two surfaces:

* ``GET/PUT /v1/settings`` — admin-authenticated. Used by the UI Settings page.
* ``GET /v1/runtime`` — proxy-authenticated (Bearer token); the in-pod proxy
  polls this. Token-protected so LAN clients hitting the API through Caddy
  cannot read thresholds or global lists.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import runtime
from ..auth import require_proxy_factory
from ..schemas import RuntimeSettings

_ALLOWED_TOP = {"inspect", "lists", "text", "image", "notifications"}
_ALLOWED_INSPECT = {"image", "video", "text", "domain", "url"}
_ALLOWED_LISTS = {
    "global_allow_domains",
    "enforce_global_allowlist",
    "global_deny_domains",
    "global_deny_keywords",
}
_ALLOWED_TEXT = {"nsfw_threshold"}
_ALLOWED_IMAGE = {"sexy_threshold", "porn_threshold", "hentai_threshold"}
_ALLOWED_NOTIFICATIONS = {
    "enabled",
    "ntfy_url",
    "webhook_url",
    "webhook_token",
    "on_block",
    "on_quarantine",
    "on_panic",
}
_ALLOWED_NESTED: dict[str, set[str]] = {
    "inspect": _ALLOWED_INSPECT,
    "lists": _ALLOWED_LISTS,
    "text": _ALLOWED_TEXT,
    "image": _ALLOWED_IMAGE,
    "notifications": _ALLOWED_NOTIFICATIONS,
}


def _validate_patch(patch: dict[str, Any]) -> None:
    if not isinstance(patch, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "patch must be an object")
    unknown = set(patch) - _ALLOWED_TOP
    if unknown:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown settings keys: {', '.join(sorted(unknown))}",
        )
    for key, value in patch.items():
        if not isinstance(value, dict):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"{key} must be an object")
        allowed = _ALLOWED_NESTED.get(key, set())
        unknown_nested = set(value) - allowed
        if unknown_nested:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown keys in {key}: {', '.join(sorted(unknown_nested))}",
            )
        # Type validation per section
        if key == "inspect":
            for k, v in value.items():
                if not isinstance(v, bool):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST, f"inspect.{k} must be a boolean"
                    )
        elif key == "lists":
            for k, v in value.items():
                if k == "enforce_global_allowlist" and not isinstance(v, bool):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "lists.enforce_global_allowlist must be a boolean",
                    )
                elif k in (
                    "global_allow_domains",
                    "global_deny_domains",
                    "global_deny_keywords",
                ) and (not isinstance(v, list) or not all(isinstance(x, str) for x in v)):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"lists.{k} must be a list of strings",
                    )
        elif key == "text":
            for k, v in value.items():
                if k == "nsfw_threshold" and (
                    isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= float(v) <= 1
                ):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        "text.nsfw_threshold must be a number between 0 and 1",
                    )
        elif key == "image":
            for k, v in value.items():
                if k in ("sexy_threshold", "porn_threshold", "hentai_threshold") and (
                    isinstance(v, bool) or not isinstance(v, int | float) or not 0 <= float(v) <= 1
                ):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"image.{k} must be a number between 0 and 1",
                    )
        elif key == "notifications":
            for k, v in value.items():
                if k in ("enabled", "on_block", "on_quarantine", "on_panic"):
                    if not isinstance(v, bool):
                        raise HTTPException(
                            status.HTTP_400_BAD_REQUEST,
                            f"notifications.{k} must be a boolean",
                        )
                elif (
                    k in ("ntfy_url", "webhook_url", "webhook_token")
                    and v is not None
                    and not isinstance(v, str)
                ):
                    raise HTTPException(
                        status.HTTP_400_BAD_REQUEST,
                        f"notifications.{k} must be a string or null",
                    )


def make_router(get_session_dep, require_admin, get_config) -> APIRouter:
    r = APIRouter(prefix="/v1", tags=["settings"])
    require_proxy = require_proxy_factory(get_config)

    @r.get("/settings", response_model=RuntimeSettings)
    def get_settings(
        session: Session = Depends(get_session_dep),
        _: str = Depends(require_admin),
    ) -> dict[str, Any]:
        return runtime.get_runtime(session)

    @r.put("/settings", response_model=RuntimeSettings)
    def put_settings(
        patch: dict[str, Any],
        session: Session = Depends(get_session_dep),
        _: str = Depends(require_admin),
    ) -> dict[str, Any]:
        _validate_patch(patch)
        return runtime.update_runtime(session, patch)

    @r.get("/runtime", response_model=RuntimeSettings)
    def get_runtime_public(
        session: Session = Depends(get_session_dep),
        _proxy: str = Depends(require_proxy),
    ) -> dict[str, Any]:
        return runtime.get_runtime(session)

    return r
