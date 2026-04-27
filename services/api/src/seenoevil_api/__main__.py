"""``python -m seenoevil_api`` and ``seenoevil-api`` entrypoint."""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    host = os.environ.get("SEENOEVIL_API_HOST", "0.0.0.0")
    port = int(os.environ.get("SEENOEVIL_API_PORT", "8000"))
    uvicorn.run(
        "seenoevil_api.app:app_factory",
        factory=True,
        host=host,
        port=port,
        log_level=os.environ.get("SEENOEVIL_API_LOG_LEVEL", "info"),
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
