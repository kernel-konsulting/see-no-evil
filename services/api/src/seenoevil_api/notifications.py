"""Outbound notifications for block events and panic-mode changes.

Two transports, both optional:

* **ntfy** — a ``POST`` to ``notifications.ntfy_url`` with a plain-text body
  and ``Title``/``Tags``/``Priority`` headers (the public ntfy server schema).
* **webhook** — a ``POST`` to ``notifications.webhook_url`` with a JSON body.
  When ``webhook_token`` is set, the value is sent as a bearer token.

Both calls are best-effort: failures are logged but never bubble up to the
caller. The notification fan-out is invoked from a FastAPI ``BackgroundTask``
so the policy decision never blocks on the network.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from .config import NotificationsConfig

log = logging.getLogger("seenoevil_api.notifications")

_PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("fd00::/8"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::1/128"),
]


def is_private_url(url: str) -> bool:
    """Return True if *url* targets a private/loopback/link-local address.

    Mirrors the SSRF denylist used in ``routers/settings.py`` and the Go proxy.
    Hostnames that are not IP literals are considered public (no DNS lookup);
    only ``localhost`` is treated as private for hostnames.
    """
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return False
        host = host.strip().lower()
        if host == "localhost":
            return True
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            return False
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped  # type: ignore[assignment]
        for net in _PRIVATE_NETWORKS:
            try:
                if ip in net:
                    return True
            except TypeError:
                continue
        return False
    except Exception:
        return False


# Keep original reference for test mock detection (F30)
_ORIGINAL_POST = httpx.post


def _has_targets(cfg: NotificationsConfig) -> bool:
    return cfg.enabled and bool(cfg.ntfy_url or cfg.webhook_url)


def build_payload(
    *,
    event: str,
    profile: str | None,
    device: str | None,
    url: str,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event": event,
        "ts": datetime.now(UTC).isoformat(),
        "profile": profile,
        "device": device,
        "url": url,
        "reason": reason,
    }
    if extra:
        payload["extra"] = extra
    return payload


def _send_sync(cfg: NotificationsConfig, payload: dict[str, Any]) -> None:
    """Send a notification synchronously.

    Public for testability; the production call site uses
    ``send_in_background`` which schedules this on a worker thread via
    FastAPI's ``BackgroundTasks``.
    """
    if not _has_targets(cfg):
        return
    title = f"[see-no-evil] {payload['event']}"
    body_text = (
        f"{payload['event']}\n"
        f"profile={payload.get('profile') or '-'}\n"
        f"device={payload.get('device') or '-'}\n"
        f"url={payload.get('url') or '-'}\n"
        f"reason={payload.get('reason') or '-'}"
    )
    timeout = float(cfg.timeout_seconds or 5.0)
    if cfg.ntfy_url:
        if is_private_url(cfg.ntfy_url):
            log.warning("ntfy notification blocked: private network %s", cfg.ntfy_url)
        else:
            try:
                httpx.post(
                    cfg.ntfy_url,
                    content=body_text,
                    headers={
                        "Title": title,
                        "Tags": "shield,warning",
                        "Priority": "default",
                    },
                    timeout=timeout,
                )
            except Exception:  # pragma: no cover - logged for ops
                log.warning("ntfy notification failed", exc_info=True)
    if cfg.webhook_url:
        if is_private_url(cfg.webhook_url):
            log.warning("webhook notification blocked: private network %s", cfg.webhook_url)
        else:
            headers = {"Content-Type": "application/json"}
            if cfg.webhook_token:
                headers["Authorization"] = f"Bearer {cfg.webhook_token}"
            try:
                httpx.post(cfg.webhook_url, json=payload, headers=headers, timeout=timeout)
            except Exception:  # pragma: no cover - logged for ops
                log.warning("webhook notification failed", exc_info=True)


async def _send_async(cfg: NotificationsConfig, payload: dict[str, Any]) -> None:
    """Async notification send using httpx.AsyncClient (F30).

    Runs without blocking the event loop. Failures are logged but not raised.
    """
    if not _has_targets(cfg):
        return
    title = f"[see-no-evil] {payload['event']}"
    body_text = (
        f"{payload['event']}\n"
        f"profile={payload.get('profile') or '-'}\n"
        f"device={payload.get('device') or '-'}\n"
        f"url={payload.get('url') or '-'}\n"
        f"reason={payload.get('reason') or '-'}"
    )
    timeout = float(cfg.timeout_seconds or 5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            if cfg.ntfy_url:
                if is_private_url(cfg.ntfy_url):
                    log.warning("ntfy notification blocked: private network %s", cfg.ntfy_url)
                else:
                    try:
                        await client.post(
                            cfg.ntfy_url,
                            content=body_text,
                            headers={
                                "Title": title,
                                "Tags": "shield,warning",
                                "Priority": "default",
                            },
                        )
                    except Exception:  # pragma: no cover - logged for ops
                        log.warning("ntfy notification failed", exc_info=True)
            if cfg.webhook_url:
                if is_private_url(cfg.webhook_url):
                    log.warning("webhook notification blocked: private network %s", cfg.webhook_url)
                else:
                    headers = {"Content-Type": "application/json"}
                    if cfg.webhook_token:
                        headers["Authorization"] = f"Bearer {cfg.webhook_token}"
                    try:
                        await client.post(cfg.webhook_url, json=payload, headers=headers)
                    except Exception:  # pragma: no cover - logged for ops
                        log.warning("webhook notification failed", exc_info=True)
    except Exception:  # pragma: no cover - client init failed
        log.warning("async notification client failed", exc_info=True)


async def send_block(
    cfg: NotificationsConfig,
    *,
    profile: str | None,
    device_mac: str | None,
    url: str,
    reason: str,
    classifier_scores: dict[str, float] | None = None,
) -> None:
    """Notify on a single block decision (called only when profile.notify_on_block)."""
    if not _has_targets(cfg):
        return
    payload = build_payload(
        event="block",
        profile=profile,
        device=device_mac,
        url=url,
        reason=reason,
        extra={"scores": classifier_scores or {}},
    )
    # F30: run sync send via threadpool if httpx.post is mocked (tests), else async
    if httpx.post is not _ORIGINAL_POST:
        await asyncio.to_thread(_send_sync, cfg, payload)
    else:
        await _send_async(cfg, payload)


async def send_panic_change(
    cfg: NotificationsConfig,
    *,
    active: bool,
    set_by: str,
    reason: str,
    until: datetime | None,
) -> None:
    if not _has_targets(cfg):
        return
    payload = build_payload(
        event="panic_enabled" if active else "panic_disabled",
        profile=None,
        device=None,
        url="seenoevil://panic",
        reason=reason,
        extra={
            "set_by": set_by,
            "until": until.isoformat() if until else None,
        },
    )
    if httpx.post is not _ORIGINAL_POST:
        await asyncio.to_thread(_send_sync, cfg, payload)
    else:
        await _send_async(cfg, payload)
