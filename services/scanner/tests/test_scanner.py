"""Tests for the scanner module (no real nmap / network calls)."""

from __future__ import annotations

import httpx
import pytest
from seenoevil_scanner.scanner import (
    Discovered,
    ScannerConfig,
    load_config,
    parse_nmap_output,
    report_to_api,
)

# ---------------------------------------------------------------------------
# nmap output parsing
# ---------------------------------------------------------------------------


_SAMPLE_NMAP = """\
Starting Nmap 7.93 ( https://nmap.org )
Nmap scan report for router.lan (192.168.1.1)
Host is up (0.0011s latency).
MAC Address: AA:BB:CC:DD:EE:01 (Some Vendor Inc.)
Nmap scan report for 192.168.1.42
Host is up (0.0023s latency).
MAC Address: AA:BB:CC:DD:EE:42 (Apple, Inc.)
Nmap scan report for printer (192.168.1.99)
Host is up.
MAC Address: AA:BB:CC:DD:EE:99 (HP Inc.)
Nmap done: 256 IP addresses (3 hosts up) scanned in 1.23 seconds
"""


def test_parse_nmap_output_basic():
    devices = parse_nmap_output(_SAMPLE_NMAP)
    assert len(devices) == 3
    assert devices[0].mac == "aa:bb:cc:dd:ee:01"
    assert devices[0].ip == "192.168.1.1"
    assert devices[0].hostname == "router.lan"
    assert devices[0].vendor == "Some Vendor Inc."
    assert devices[1].hostname is None  # no PTR name in line
    assert devices[1].ip == "192.168.1.42"
    assert devices[2].hostname == "printer"


def test_parse_nmap_output_empty():
    assert parse_nmap_output("") == []


def test_parse_nmap_output_skips_hosts_without_mac():
    text = "Nmap scan report for 192.168.1.1\nHost is up.\n"
    assert parse_nmap_output(text) == []


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------


def test_load_config_defaults_when_no_file(monkeypatch):
    monkeypatch.delenv("SEENOEVIL_CONFIG", raising=False)
    monkeypatch.delenv("CONFIG_PATH", raising=False)
    cfg = load_config()
    assert cfg.enabled is False
    assert cfg.cidr == "192.168.1.0/24"
    assert cfg.interval_seconds == 3600


def test_load_config_reads_yaml(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text("scanner:\n  enabled: true\n  cidr: 10.0.0.0/24\n  interval: 30m\n")
    monkeypatch.setenv("SEENOEVIL_CONFIG", str(p))
    cfg = load_config()
    assert cfg.enabled is True
    assert cfg.cidr == "10.0.0.0/24"
    assert cfg.interval_seconds == 1800


def test_load_config_env_overrides(monkeypatch, tmp_path):
    monkeypatch.delenv("SEENOEVIL_CONFIG", raising=False)
    monkeypatch.setenv("API_BASE", "http://api.example:8000/")
    monkeypatch.setenv("API_TOKEN", "tok123")
    monkeypatch.setenv("METRICS_PORT", "9999")
    cfg = load_config()
    assert cfg.api_base == "http://api.example:8000"
    assert cfg.api_token == "tok123"
    assert cfg.metrics_port == 9999


# ---------------------------------------------------------------------------
# API client
# ---------------------------------------------------------------------------


def test_report_to_api_posts_payload():
    cfg = ScannerConfig(api_base="http://api:8000")
    devices = [
        Discovered(mac="aa:bb:cc:dd:ee:01", ip="10.0.0.1", hostname="r"),
        Discovered(mac="aa:bb:cc:dd:ee:02", ip="10.0.0.2"),
    ]

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["json"] = request.read().decode()
        return httpx.Response(
            200,
            json={
                "profile_id": 1,
                "profile_name": "guests",
                "items": [
                    {"mac": "aa:bb:cc:dd:ee:01", "device_id": 1, "created": True},
                    {"mac": "aa:bb:cc:dd:ee:02", "device_id": 2, "created": True},
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as client:
        result = report_to_api(cfg, devices, client=client)

    assert captured["url"].endswith("/v1/devices/discover")
    assert "aa:bb:cc:dd:ee:01" in captured["json"]
    assert result["profile_name"] == "guests"
    assert len(result["items"]) == 2


def test_report_to_api_raises_on_http_error():
    cfg = ScannerConfig(api_base="http://api:8000")
    transport = httpx.MockTransport(lambda r: httpx.Response(503, json={"detail": "no profile"}))
    with httpx.Client(transport=transport) as client, pytest.raises(httpx.HTTPStatusError):
        report_to_api(cfg, [Discovered(mac="aa:bb:cc:dd:ee:01")], client=client)
