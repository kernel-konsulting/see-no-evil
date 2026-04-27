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
| `API_BASE` | `http://api:8000` | API base URL |
| `API_TOKEN` | _(unset)_ | Bearer token for the API (when configured) |
| `METRICS_PORT` | `9102` | Prometheus metrics endpoint |
| `LOG_LEVEL` | `INFO` | Log level (DEBUG/INFO/WARNING/ERROR) |
| `SCANNER_CIDR` | `192.168.1.0/24` | Override CIDR if config absent |
| `SCANNER_INTERVAL_SECONDS` | `3600` | Override interval if config absent |

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
