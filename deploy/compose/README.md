# Compose deployment (Podman-native)

The `docker-compose.yml` in this directory is the canonical reference
deployment and works with **Podman** as well as Docker. It is organized by
**profiles** so a small home install pulls only the containers it needs:

| Command (Podman) | What you get |
|---|---|
| `podman compose --profile core up -d` | DNS + proxy + classifiers + API + UI + Caddy + updater |
| `podman compose --profile core --profile scanner up -d` | + nmap scanner |
| `podman compose --profile core --profile postgres --profile redis up -d` | Org build (Postgres + Redis) |
| `podman compose --profile core --profile observability up -d` | + metrics / dashboards |
| `podman compose --profile core --profile vpn-tailscale up -d` | + Tailscale |
| `podman compose --profile core --profile vpn-wg up -d` | + wg-easy |
| `podman compose -f docker-compose.yml -f docker-compose.gpu.yml --profile core up -d` | GPU classifiers |

Docker variant (legacy): replace `podman compose` with `docker compose`. For podman-compose
standalone: `podman-compose --profile core up -d`.

DNS host-port mapping is configurable for rootless Podman compatibility:

- `SNE_DNS_PORT_UDP` (default `1053`)
- `SNE_DNS_PORT_TCP` (default `1053`)

Set both to `53` if you run with privileges that allow binding low ports.

**M0 reminder:** every service image is a stub that just sleeps. This proves the
topology and networking; functional behavior lands in M1.

## Secrets

Compose secrets are file-based and live under `./secrets/` (gitignored). At
minimum, if you enable the `postgres` profile, create
`./secrets/postgres_password.txt` with a random string. Same for
`./secrets/tailscale_authkey.txt` if you enable Tailscale.

## Network model

Two Docker networks:

- `edge` — DNS, proxy, Caddy, updater. Reachable from the LAN.
- `internal` — API, UI backend, classifiers, optional Postgres / Redis.
  `internal: true`, so containers on this network **cannot** reach the
  internet. This is enforcement of the "no inspected content leaves the box"
  rule from `docs/architecture.md`.

The `proxy` and `updater` containers straddle both networks because they are
the only components that need both LAN access and outbound HTTP.

## Hardening — transparent mode (recommended for appliance)

Explicit proxy is opt-in — clients can disable it to bypass filtering. For a
strict appliance run transparent interception:

```bash
# DNS — force all LAN DNS through Blocky (DNAT 53)
iptables -t nat -A PREROUTING -i br0 -p udp --dport 53 -j DNAT --to 192.168.1.1:53
iptables -t nat -A PREROUTING -i br0 -p tcp --dport 53 -j DNAT --to 192.168.1.1:53
# QUIC / DoH / DoT — block UDP 443 and 853 so browsers fall back to TCP
iptables -A FORWARD -p udp --dport 443 -j DROP
iptables -A FORWARD -p udp --dport 853 -j DROP
iptables -A FORWARD -p tcp --dport 853 -j DROP
# HTTP/HTTPS — redirect to MITM proxy (requires proxy to run in host net or with
# TPROXY). When using Podman host networking, add:
#   iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to 8080
#   iptables -t nat -A PREROUTING -p tcp --dport 443 -j REDIRECT --to 8080
```

Set `proxy.fail_closed: true` in `config.yaml` so classifier/API outages block
rather than silently allow. Podman: `podman compose --profile core` already uses
`podman` networks with `internal:true` for classifier/API isolation.
