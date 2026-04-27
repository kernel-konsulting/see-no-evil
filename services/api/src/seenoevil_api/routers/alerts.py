"""``/v1/alerts/webhook`` — receives vmalert payloads and fans them out.

vmalert posts a JSON document like::

    {
      "alerts": [
        {
          "status": "firing",
          "labels": {"alertname": "ProxyDown", "severity": "critical", ...},
          "annotations": {"summary": "..."},
          "startsAt": "2026-01-01T00:00:00Z",
          ...
        }
      ]
    }

Each alert is reshaped into the same notification payload used for block
events and forwarded via ntfy / webhook (whatever ``notifications:`` in
config says). No DB writes — alerts are ephemeral and re-fired by vmalert
on every evaluation while the condition holds.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, status

from .. import notifications
from ..config import AppConfig


def make_router(get_config) -> APIRouter:
    r = APIRouter(prefix="/v1/alerts", tags=["alerts"])

    @r.post("/webhook", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
    def receive_webhook(
        body: dict[str, Any],
        background: BackgroundTasks,
        config: AppConfig = Depends(get_config),
    ) -> None:
        cfg = config.notifications
        if not cfg.enabled:
            return
        for alert in body.get("alerts", []) or []:
            labels = alert.get("labels") or {}
            annotations = alert.get("annotations") or {}
            payload = notifications.build_payload(
                event=f"alert_{alert.get('status', 'firing')}",
                profile=labels.get("service"),
                device=None,
                url=f"seenoevil://alert/{labels.get('alertname', 'unknown')}",
                reason=annotations.get("summary") or labels.get("alertname", "alert"),
                extra={
                    "severity": labels.get("severity"),
                    "description": annotations.get("description"),
                    "starts_at": alert.get("startsAt"),
                },
            )
            background.add_task(notifications._send_sync, cfg, payload)
        return

    return r
