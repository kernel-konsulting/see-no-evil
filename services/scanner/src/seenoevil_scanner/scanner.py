"""Periodic LAN sweep + report to API."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml
from prometheus_client import Counter, Gauge, start_http_server

log = logging.getLogger("seenoevil_scanner")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


DEFAULT_INTERVAL_SECONDS = 3600
DEFAULT_CIDR = "192.168.1.0/24"


@dataclass
class ScannerConfig:
    enabled: bool = False
    cidr: str = DEFAULT_CIDR
    interval_seconds: int = DEFAULT_INTERVAL_SECONDS
    api_base: str = "http://api:8000"
    api_token: str | None = None
    metrics_port: int = 9102
    nmap_args: list[str] = field(default_factory=lambda: ["-sn", "-PR", "-n"])
    ready_path: str = "/tmp/scanner_ready"


_DURATION_RE = re.compile(r"^\s*(\d+)\s*(s|m|h|d)\s*$")


def _parse_duration(value: str | int | None, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    m = _DURATION_RE.match(str(value))
    if not m:
        return default
    n, unit = int(m.group(1)), m.group(2)
    return n * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


def load_config(path: str | os.PathLike[str] | None = None) -> ScannerConfig:
    raw: dict[str, Any] = {}
    if path is None:
        path = os.environ.get("SEENOEVIL_CONFIG") or os.environ.get("CONFIG_PATH")
    if path:
        p = Path(path)
        if p.exists():
            raw = yaml.safe_load(p.read_text()) or {}
    sec = (raw.get("scanner") or {}) if isinstance(raw, dict) else {}
    return ScannerConfig(
        enabled=bool(sec.get("enabled", False)),
        cidr=str(sec.get("cidr") or os.environ.get("SCANNER_CIDR") or DEFAULT_CIDR),
        interval_seconds=_parse_duration(
            sec.get("interval"),
            int(os.environ.get("SCANNER_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        ),
        api_base=os.environ.get("API_BASE", "http://api:8000").rstrip("/"),
        api_token=os.environ.get("API_TOKEN") or None,
        metrics_port=int(os.environ.get("METRICS_PORT", 9102)),
    )


# ---------------------------------------------------------------------------
# nmap wrapper
# ---------------------------------------------------------------------------


@dataclass
class Discovered:
    mac: str
    ip: str | None = None
    hostname: str | None = None
    vendor: str | None = None


def run_nmap(cidr: str, extra_args: list[str] | None = None) -> str:
    """Run an nmap ARP-ping sweep and return its stdout."""
    args = ["nmap", *(extra_args or ["-sn", "-PR", "-n"]), cidr]
    log.info("running %s", " ".join(args))
    proc = subprocess.run(  # noqa: S603 - args are operator-controlled
        args, capture_output=True, text=True, timeout=300, check=False
    )
    if proc.returncode != 0:
        log.warning("nmap exited %d: %s", proc.returncode, proc.stderr.strip())
    return proc.stdout


# Two regexes to scrape the relevant lines from `nmap -sn` text output.
_RE_REPORT = re.compile(r"^Nmap scan report for (?:(\S+) \()?([\d.]+)\)?")
_RE_MAC = re.compile(r"^MAC Address: ([0-9A-F:]{17}) \(([^)]*)\)", re.IGNORECASE)


def parse_nmap_output(text: str) -> list[Discovered]:
    """Pull (MAC, IP, hostname, vendor) tuples out of nmap's text output."""
    devices: list[Discovered] = []
    pending_ip: str | None = None
    pending_host: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        m = _RE_REPORT.match(line)
        if m:
            pending_host = m.group(1)
            pending_ip = m.group(2)
            continue
        m = _RE_MAC.match(line)
        if m and pending_ip is not None:
            mac = m.group(1).lower()
            vendor = m.group(2).strip() or None
            devices.append(
                Discovered(
                    mac=mac,
                    ip=pending_ip,
                    hostname=pending_host,
                    vendor=vendor,
                )
            )
            pending_ip = None
            pending_host = None
    return devices


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


def report_to_api(
    cfg: ScannerConfig, devices: list[Discovered], client: httpx.Client | None = None
) -> dict[str, Any]:
    payload = {
        "devices": [
            {
                "mac": d.mac,
                "ip": d.ip,
                "hostname": d.hostname,
                "vendor": d.vendor,
            }
            for d in devices
        ]
    }
    headers = {}
    if cfg.api_token:
        headers["Authorization"] = f"Bearer {cfg.api_token}"
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=30.0)
    try:
        resp = client.post(
            f"{cfg.api_base}/v1/devices/discover",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()
    finally:
        if own_client:
            client.close()


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------


_SCAN_TOTAL = Counter("scanner_scans_total", "Number of scans performed")
_SCAN_ERRORS = Counter("scanner_errors_total", "Errors encountered during scans")
_DEVICES_SEEN = Gauge("scanner_devices_seen", "Devices discovered in last scan")
_LAST_SCAN_TS = Gauge("scanner_last_scan_unixtime", "Unix timestamp of last scan")


_running = True


def _signal_handler(signum: int, _frame: object) -> None:
    global _running
    log.info("received signal %d, exiting after current scan", signum)
    _running = False


def _setup_logging() -> None:
    level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def _mark_ready(path: str) -> None:
    with contextlib.suppress(OSError):
        Path(path).touch()


def main() -> None:
    _setup_logging()
    cfg = load_config()

    if not cfg.enabled:
        log.info("scanner disabled in config; sleeping (set scanner.enabled: true to enable)")
        _mark_ready(cfg.ready_path)
        signal.signal(signal.SIGTERM, _signal_handler)
        signal.signal(signal.SIGINT, _signal_handler)
        while _running:
            time.sleep(60)
        return

    log.info(
        "scanner starting: cidr=%s interval=%ds api=%s",
        cfg.cidr,
        cfg.interval_seconds,
        cfg.api_base,
    )
    start_http_server(cfg.metrics_port)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    _mark_ready(cfg.ready_path)

    while _running:
        _SCAN_TOTAL.inc()
        try:
            output = run_nmap(cfg.cidr, cfg.nmap_args)
            devices = parse_nmap_output(output)
            _DEVICES_SEEN.set(len(devices))
            log.info("scan complete: %d devices", len(devices))
            if devices:
                result = report_to_api(cfg, devices)
                created = sum(1 for i in result.get("items", []) if i.get("created"))
                log.info("reported %d devices to api (%d new)", len(devices), created)
        except subprocess.TimeoutExpired:
            _SCAN_ERRORS.inc()
            log.exception("nmap timed out")
        except httpx.HTTPError as exc:
            _SCAN_ERRORS.inc()
            log.warning("api report failed: %s", exc)
        except Exception:  # noqa: BLE001
            _SCAN_ERRORS.inc()
            log.exception("scan failed")
        _LAST_SCAN_TS.set(time.time())

        # Sleep in 1s ticks so SIGTERM is responsive.
        for _ in range(cfg.interval_seconds):
            if not _running:
                break
            time.sleep(1)

    log.info("scanner exiting cleanly")
    sys.exit(0)


if __name__ == "__main__":
    main()
