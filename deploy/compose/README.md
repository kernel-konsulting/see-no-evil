# Compose deployment

The `docker-compose.yml` in this directory is the canonical reference
deployment. It is organized by **profiles** so a small home install pulls only
the containers it needs:

| Command | What you get |
|---|---|
| `docker compose --profile core up -d` | DNS + proxy + classifiers + API + UI + Caddy + updater |
| `docker compose --profile core --profile scanner up -d` | + nmap scanner |
| `docker compose --profile core --profile postgres --profile redis up -d` | Org build (Postgres + Redis) |
| `docker compose --profile core --profile observability up -d` | + metrics / dashboards |
| `docker compose --profile core --profile vpn-tailscale up -d` | + Tailscale |
| `docker compose --profile core --profile vpn-wg up -d` | + wg-easy |
| `docker compose -f docker-compose.yml -f docker-compose.gpu.yml --profile core up -d` | GPU classifiers |

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
