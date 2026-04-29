"""On-demand LAN scan trigger.

Forwards a request to the scanner sidecar's control plane (see
``services/scanner``) which runs an nmap sweep and POSTs the results back to
``/v1/devices/discover``.

Configurable via ``SEENOEVIL_SCANNER_URL`` (default ``http://127.0.0.1:9104``).
"""

from __future__ import annotations

import os

import httpx
from fastapi import APIRouter, Depends, HTTPException

SCANNER_URL_ENV = "SEENOEVIL_SCANNER_URL"
DEFAULT_SCANNER_URL = "http://127.0.0.1:9104"


def make_router(require_admin) -> APIRouter:
    r = APIRouter(prefix="/v1/scanner", tags=["scanner"])

    @r.post("/scan", dependencies=[Depends(require_admin)])
    def trigger_scan() -> dict[str, object]:
        url = os.environ.get(SCANNER_URL_ENV, DEFAULT_SCANNER_URL).rstrip("/")
        try:
            with httpx.Client(timeout=120.0) as client:
                resp = client.post(f"{url}/scan")
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"scanner sidecar unreachable at {url}: {exc}. "
                    "Make sure the scanner container is running in the pod."
                ),
            ) from exc
        try:
            payload: dict[str, object] = resp.json()
        except ValueError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"scanner returned non-JSON ({resp.status_code}): {resp.text[:200]}",
            ) from exc
        if resp.status_code >= 400 or not payload.get("ok"):
            raise HTTPException(status_code=502, detail=payload)
        return payload

    @r.get("/health", dependencies=[Depends(require_admin)])
    def scanner_health() -> dict[str, object]:
        url = os.environ.get(SCANNER_URL_ENV, DEFAULT_SCANNER_URL).rstrip("/")
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(f"{url}/healthz")
            return {"reachable": True, "status_code": resp.status_code}
        except httpx.HTTPError as exc:
            return {"reachable": False, "error": str(exc)}

    return r
