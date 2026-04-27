# Scanner (optional)

Periodic LAN device discovery. Runs on the schedule defined in
`scanner.interval` and posts results to the API for display in the UI.

**Disabled by default.** Enable with `scanner.enabled: true`.

## What it does

- `nmap -sn` host discovery on each CIDR in `scanner.cidrs`.
- Optional `nmap -sV --top-ports 100` if `scanner.profile: light`.
- Passive mDNS / SSDP listening for friendly device names.
- Vendor lookup via the IEEE OUI database (cached in `/data/oui.csv`).

## What it does NOT do

- Send any data outside the LAN.
- Run continuously — only on the configured schedule.
- Probe sensitive ports / vulnerability scan.

## Capabilities required

`CAP_NET_RAW` (for raw ICMP) and `CAP_NET_ADMIN` (for ARP). Granted in
`deploy/compose/scanner.yml`. On Kubernetes this needs `hostNetwork: true` or a
suitable CNI. Documented in [`docs/architecture.md`](../../docs/architecture.md).
