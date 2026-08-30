# Scanner

Periodic LAN device discovery for see-no-evil. Sweeps the configured CIDR with
`nmap -sn -PR` (ARP ping, no port scan) and reports each discovered device to
the API at `POST /v1/devices/discover`. New MACs are auto-assigned to the
configured `devices.default_profile`.

## Configuration

Reads from the shared `config.yaml`:

```yaml
scanner:
  enabled: false                # off by default
  cidr: 192.168.1.0/24
  interval: 1h                  # supports s/m/h/d suffixes
```

Environment variable overrides:

| Variable | Default | Purpose |
|---|---|---|
| `API_BASE` | `http://api:8000` | API base URL (auto-fallback to `API_BASE_HOST` when `SCANNER_HOST_NETWORK=1`) |
| `API_TOKEN` | _(unset)_ | Bearer token for the API (when configured) |
| `METRICS_PORT` | `9102` | Prometheus metrics endpoint |
| `LOG_LEVEL` | `INFO` | Log level (DEBUG/INFO/WARNING/ERROR) |
| `SCANNER_CIDR` | `192.168.1.0/24` | Override CIDR if config absent |
| `SCANNER_INTERVAL_SECONDS` | `3600` | Override interval if config absent |
| `SCANNER_HOST_NETWORK` | `0` | `1` when scanner runs with `network_mode: host` — enables `API_BASE_HOST` fallback |
| `API_BASE_HOST` | `http://127.0.0.1:8000` | Host-accessible API base for `SCANNER_HOST_NETWORK=1` |
| `SCANNER_TOKEN` | _(unset)_ | Token for control plane `POST /scan` (fail-closed when unset) |

## Capabilities required

`nmap -sn -PR` uses raw sockets and needs `CAP_NET_RAW` + `CAP_NET_ADMIN`.
The compose service also uses `network_mode: host` so it can see the LAN.

If you want to avoid privileged networking, two alternatives exist (not yet
implemented — issues welcome):

1. **macvlan attachment**: give the scanner its own LAN IP via macvlan and
   keep the rest of the stack on bridged networks.
2. **Passive ARP table parsing**: read `/proc/net/arp` from the host. Only
   sees devices that have already been talked to, but needs no caps.

## Metrics

| Metric | Type | Description |
|---|---|---|
| `scanner_scans_total` | counter | Number of completed scan iterations |
| `scanner_errors_total` | counter | Errors during scan or report |
| `scanner_devices_seen` | gauge | Devices discovered in the last scan |
| `scanner_last_scan_unixtime` | gauge | Unix timestamp of the last scan |

## Building

```bash
cd services/scanner
pip install -e ".[test]"
pytest
```

## Status

**M1.6 implemented.** Discovery + reporting working; uses nmap `-sn -PR -n`
under the hood. Future work tracked in PLAN.md (M7 scanner UI).

**M12 hardening (whole-codebase):**

- `API_BASE` host-network fallback now via `urlparse(hostname=="api")` not string `==`.
- `perform_scan` uses `try-lock` (`acquire(blocking=False)`) -> `429 scan busy` instead of blocking 300s.
- Control plane `/scan` now fail-closed (`401` when `SCANNER_TOKEN` unset) even on `127.0.0.1`.
- Updater retry not in scanner but scanner now logs `host-network mode: using API base` at `INFO`.

**M13 hardening:**

- `_detect_local_cidr` now parses `ip -4 -o addr` `inet a.b.c.d/prefix` and `ipaddress.ip_network(..., strict=False)` → correct `10.88.0.0/16` instead of `/24` (F28).
- `report_to_api` reuses `httpx.Client` with `follow_redirects=False` and validates `SCANNER_API_TOKEN` allow-list.
