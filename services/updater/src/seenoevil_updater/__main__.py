"""Entry point.

Runs the update cycle once on startup, then sleeps for SNE_UPDATE_INTERVAL
seconds (default 24h) and runs again. Exits non-zero only on unrecoverable
errors so the container restart policy doesn't hot-loop on transient failures.
"""

import logging
import os
import time

from .updater import run

log = logging.getLogger("updater")

DEFAULT_INTERVAL_SECONDS = 24 * 60 * 60  # 24h


def main() -> None:
    interval = int(os.environ.get("SNE_UPDATE_INTERVAL", DEFAULT_INTERVAL_SECONDS))
    while True:
        try:
            run()
        except Exception:  # noqa: BLE001 — keep the daemon alive
            log.exception("update cycle failed; will retry after interval")
        if interval <= 0:
            return
        log.info("sleeping %d seconds until next update cycle", interval)
        time.sleep(interval)


if __name__ == "__main__":
    main()
