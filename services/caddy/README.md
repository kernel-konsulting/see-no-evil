# see-no-evil — Caddy admin UI reverse proxy
#
# Handles TLS termination for the admin console (separate from the MITM CA).
#
# Environment variables:
#   SNE_HOSTNAME  — LAN hostname (default: seenoevil.lan)
#   SNE_TLS_MODE  — internal | acme | off (default: internal)
#   SNE_TLS_EMAIL — required for acme mode

## Ports

| Port | Purpose |
|---|---|
| 80 | HTTP (redirects to 443 in default config) |
| 443 | HTTPS (internal CA or ACME) |

## TLS modes

**`internal`** (default): Caddy generates a self-signed CA and issues a cert for `SNE_HOSTNAME`.  
Trust the Caddy root by visiting `https://seenoevil.lan/` and importing the cert shown, or via `caddy trust`.

**`acme`**: Uses Let's Encrypt / ZeroSSL. Requires `SNE_HOSTNAME` to have a valid public DNS record pointing to this host and `SNE_TLS_EMAIL` set to a valid address.

**`off`**: HTTP only on port 80. Useful when behind another TLS terminator (e.g. nginx, Traefik, or an existing Caddy instance).
