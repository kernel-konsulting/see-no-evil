"""``python -m seenoevil_api`` and ``seenoevil-api`` entrypoint."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("SEENOEVIL_API_HOST", "0.0.0.0")
    port = int(os.environ.get("SEENOEVIL_API_PORT", "8000"))
    # Trust X-Forwarded-For from Caddy/container networks. SEENOEVIL_TRUSTED_PROXIES
    # takes precedence (supports CIDRs like 172.16.0.0/12), then CADDY_IP for
    # backwards compat, then a safe default covering loopback + Podman/Docker.
    forwarded_ips = (
        os.environ.get("SEENOEVIL_TRUSTED_PROXIES")
        or os.environ.get("CADDY_IP")
        or "127.0.0.1,172.16.0.0/12,10.88.0.0/16"
    )
    # CADDY_IP may be a single IP without mask; uvicorn accepts comma-separated.
    uvicorn.run(
        "seenoevil_api.app:app_factory",
        factory=True,
        host=host,
        port=port,
        log_level=os.environ.get("SEENOEVIL_API_LOG_LEVEL", "info"),
        proxy_headers=True,
        forwarded_allow_ips=forwarded_ips,
    )


if __name__ == "__main__":
    main()
