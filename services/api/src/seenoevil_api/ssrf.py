"""SSRF protection helpers — single source for private-IP denylist.

This module owns the Python side of the private-network denylist so
``routers/settings.py`` and ``notifications.py`` don't drift. The set is
intentionally synced with ``services/proxy/internal/mitm/handler.go:159``
``privatePrefixes`` (the Go proxy's dial-time denylist). Any new CIDR added
to one must be added to the other.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

# Keep in sync with services/proxy/internal/mitm/handler.go privatePrefixes.
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    # Benchmark (RFC 2544) — also blocked by Go proxy
    ipaddress.ip_network("198.18.0.0/15"),
    # TEST-NET-1/2/3 — non-routable but attacker-controllable in some envs
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
    # Multicast
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("fd00::/8"),
    ipaddress.ip_network("fe80::/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("::/128"),
]


def is_private_url(url: str) -> bool:
    """Return True if *url* targets a private/loopback/link-local address.

    Hostnames that are not IP literals are considered public (no DNS lookup
    at validation time); only ``localhost`` is treated as private for hostnames.
    Runtime send-time checks in ``notifications.py`` re-apply this before any
    outbound request, and the Go proxy does DNS-aware blocking on the dial path.
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
