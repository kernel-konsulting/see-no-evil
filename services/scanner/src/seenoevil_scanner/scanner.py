"""Periodic LAN sweep + report to API."""

from __future__ import annotations

import contextlib
import hmac
import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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
    # 9101 = image-classifier, 9102 = text-classifier — pick a free port for
    # the scanner so it doesn't collide when sharing the pod's network ns.
    metrics_port: int = 9105
    control_port: int = 9104
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


def _detect_local_cidr() -> str | None:
    """Best-effort: derive a /24 from the primary non-loopback IPv4 address.

    Tries ``ip -4 -o addr`` first, falls back to ``/proc/net/route`` and
    ``/proc/net/fib_trie`` style parsing so it works in minimal images
    without iproute2.
    """
    # Primary: ip command.
    try:
        out = subprocess.run(  # noqa: S603 - fixed argv
            ["ip", "-4", "-o", "addr"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        ).stdout
        for line in out.splitlines():
            # Example: "2: eth0    inet 10.88.0.23/16 brd 10.88.255.255 scope global eth0"
            m = re.search(r"inet (\d+\.\d+\.\d+)\.\d+/\d+ .*scope global", line)
            if m:
                return f"{m.group(1)}.0/24"
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback: parse /proc/net/route for the default gateway's interface.
    try:
        with Path("/proc/net/route").open() as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3 and parts[1] == "00000000":
                    iface = parts[0]
                    # Try to get iface addr via /proc or hostname -I
                    out2 = subprocess.run(  # noqa: S603
                        ["hostname", "-I"],
                        capture_output=True,
                        text=True,
                        timeout=2,
                        check=False,
                    ).stdout
                    for token in out2.split():
                        m = re.match(r"(\d+\.\d+\.\d+)\.\d+", token.strip())
                        if m:
                            return f"{m.group(1)}.0/24"
                    log.warning("could not determine IP for iface %s, using fallback", iface)
                    break
    except (OSError, ValueError):
        pass
    return None


def load_config(path: str | os.PathLike[str] | None = None) -> ScannerConfig:
    raw: dict[str, Any] = {}
    if path is None:
        path = os.environ.get("SEENOEVIL_CONFIG") or os.environ.get("CONFIG_PATH")
    if path:
        p = Path(path)
        if p.exists():
            raw = yaml.safe_load(p.read_text()) or {}
    sec = (raw.get("scanner") or {}) if isinstance(raw, dict) else {}
    # Resolution order for CIDR:
    #   1. SCANNER_CIDR env (operator override)
    #   2. config.scanner.cidr  — but only if it's not the placeholder default
    #   3. auto-detected from the scanner container's primary interface
    #   4. hardcoded fallback
    env_cidr = os.environ.get("SCANNER_CIDR")
    cfg_cidr = sec.get("cidr")
    # Compat: config.example.yaml uses `scanner.cidrs` (list); accept both.
    if not cfg_cidr:
        cidrs_val = sec.get("cidrs")
        if isinstance(cidrs_val, list) and cidrs_val:
            cfg_cidr = str(cidrs_val[0])
        elif isinstance(cidrs_val, str) and cidrs_val:
            cfg_cidr = cidrs_val
    if env_cidr:
        cidr = env_cidr
        if env_cidr == DEFAULT_CIDR:
            log.warning("SCANNER_CIDR is default %s — verify it matches your LAN", DEFAULT_CIDR)
    elif cfg_cidr and cfg_cidr != DEFAULT_CIDR:
        cidr = str(cfg_cidr)
    else:
        detected = _detect_local_cidr()
        if detected:
            cidr = detected
            log.info("auto-detected scan CIDR: %s", cidr)
        else:
            cidr = DEFAULT_CIDR
            log.warning(
                "could not auto-detect LAN CIDR (ip/hostname missing), falling back to %s — "
                "set scanner.cidrs or SCANNER_CIDR to your LAN's subnet",
                DEFAULT_CIDR,
            )
    # When running with host networking (default for ARP scans), `api`
    # DNS name won't resolve — fall back to host-accessible address.
    _api_base_raw = os.environ.get("API_BASE", "http://api:8000").rstrip("/")
    if _api_base_raw == "http://api:8000" and os.environ.get("SCANNER_HOST_NETWORK", "0") in (
        "1",
        "true",
    ):
        _api_base_raw = os.environ.get("API_BASE_HOST", "http://127.0.0.1:8000")
        log.warning("host-network mode: using API base %s", _api_base_raw)
    return ScannerConfig(
        enabled=bool(sec.get("enabled", False)),
        cidr=cidr,
        interval_seconds=_parse_duration(
            sec.get("interval"),
            int(os.environ.get("SCANNER_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS)),
        ),
        api_base=_api_base_raw,
        api_token=os.environ.get("API_TOKEN") or None,
        metrics_port=int(os.environ.get("METRICS_PORT", 9105)),
        control_port=int(os.environ.get("CONTROL_PORT", 9104)),
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
_scan_lock = threading.Lock()


def perform_scan(cfg: ScannerConfig) -> dict[str, Any]:
    """Run a single scan and report it. Returns a result summary dict."""
    with _scan_lock:
        _SCAN_TOTAL.inc()
        started = time.time()
        try:
            output = run_nmap(cfg.cidr, cfg.nmap_args)
            devices = parse_nmap_output(output)
            _DEVICES_SEEN.set(len(devices))
            log.info("scan complete: %d devices on %s", len(devices), cfg.cidr)
            api_result: dict[str, Any] = {}
            if devices:
                api_result = report_to_api(cfg, devices)
                created = sum(1 for i in api_result.get("items", []) if i.get("created"))
                log.info("reported %d devices to api (%d new)", len(devices), created)
            _LAST_SCAN_TS.set(time.time())
            result: dict[str, Any] = {
                "ok": True,
                "cidr": cfg.cidr,
                "devices_found": len(devices),
                "devices_created": sum(1 for i in api_result.get("items", []) if i.get("created")),
                "duration_seconds": round(time.time() - started, 2),
            }
            if not devices:
                local = _detect_local_cidr()
                result["note"] = (
                    f"No devices found on {cfg.cidr}. The scanner sees network "
                    f"{local or 'unknown'} from inside its container. On rootless "
                    "Podman / Docker Desktop the container cannot ARP-discover "
                    "devices on the host LAN — deploy on a Linux gateway with "
                    "--network=host (or run on the actual router) for real LAN "
                    "discovery. You can still add devices manually."
                )
            return result
        except subprocess.TimeoutExpired:
            _SCAN_ERRORS.inc()
            log.exception("nmap timed out")
            return {"ok": False, "error": "nmap timed out"}
        except httpx.HTTPError as exc:
            _SCAN_ERRORS.inc()
            log.warning("api report failed: %s", exc)
            return {"ok": False, "error": f"api report failed: {exc}"}
        except Exception as exc:  # noqa: BLE001
            _SCAN_ERRORS.inc()
            log.exception("scan failed")
            return {"ok": False, "error": str(exc)}


class _ControlHandler(BaseHTTPRequestHandler):
    """Tiny HTTP control plane: POST /scan triggers an immediate sweep."""

    cfg: ScannerConfig | None = None  # set by start_control_server

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log.info("control: " + format, *args)

    def _json(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _check_auth(self) -> bool:
        """Return True if scanner control token matches expected value."""
        cfg = _ControlHandler.cfg
        expected = (
            os.environ.get("SCANNER_TOKEN")
            or os.environ.get("SCANNER_API_TOKEN")
            or os.environ.get("API_TOKEN")
            or (cfg.api_token if cfg else None)
        )
        if not expected:
            # No token configured — allow only because listener is localhost-only;
            # log once so operator knows to set SCANNER_TOKEN in prod (#20).
            return True
        auth = self.headers.get("Authorization", "")
        alt = self.headers.get("X-Scanner-Token", "")
        provided = ""
        if auth.startswith("Bearer "):
            provided = auth[len("Bearer ") :].strip()
        elif alt:
            provided = alt.strip()
        return bool(provided and hmac.compare_digest(provided, expected))

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(200, {"status": "ok"})
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/scan":
            if not self._check_auth():
                self._json(401, {"error": "unauthorized"})
                return
            if _ControlHandler.cfg is None:
                self._json(503, {"error": "scanner not initialised"})
                return
            result = perform_scan(_ControlHandler.cfg)
            self._json(200 if result.get("ok") else 500, result)
            return
        self._json(404, {"error": "not found"})


def start_control_server(cfg: ScannerConfig) -> ThreadingHTTPServer:
    _ControlHandler.cfg = cfg
    # Bind only to loopback; the API (on same pod) proxies via 127.0.0.1:9104.
    # Binding 0.0.0.0 would expose unauthenticated control port to LAN (#20).
    srv = ThreadingHTTPServer(("127.0.0.1", cfg.control_port), _ControlHandler)
    t = threading.Thread(target=srv.serve_forever, name="scanner-control", daemon=True)
    t.start()
    log.info("scanner control plane listening on 127.0.0.1:%d", cfg.control_port)
    return srv


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

    # Always start the control server so on-demand scans from the UI work even
    # when the periodic loop is disabled.
    try:
        start_http_server(cfg.metrics_port)
    except OSError as exc:
        log.warning("metrics port %d unavailable: %s", cfg.metrics_port, exc)
    start_control_server(cfg)
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)
    _mark_ready(cfg.ready_path)

    if not cfg.enabled:
        log.info(
            "periodic scanner disabled in config (set scanner.enabled: true to enable). "
            "On-demand scans via POST /scan still available on :%d",
            cfg.control_port,
        )
        while _running:
            time.sleep(60)
        return

    log.info(
        "scanner starting: cidr=%s interval=%ds api=%s",
        cfg.cidr,
        cfg.interval_seconds,
        cfg.api_base,
    )

    while _running:
        perform_scan(cfg)
        # Sleep in 1s ticks so SIGTERM is responsive.
        for _ in range(cfg.interval_seconds):
            if not _running:
                break
            time.sleep(1)

    log.info("scanner exiting cleanly")
    sys.exit(0)


if __name__ == "__main__":
    main()
