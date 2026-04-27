# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — caddy (admin UI reverse proxy)
#
# Caddy handles TLS termination for the admin UI:
#   https://seenoevil.lan  →  ui:80
#   https://seenoevil.lan/v1  →  api:8000
#
# The Caddyfile is rendered from the template at startup using envsubst.
# Caddy auto-provisions a TLS cert via its internal CA for LAN hostnames.
# -----------------------------------------------------------------------------
FROM caddy:2-alpine

RUN apk add --no-cache gettext  # provides envsubst

COPY services/caddy/Caddyfile.template /etc/caddy/Caddyfile.template
COPY services/caddy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 80 443

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://localhost:80/healthz || exit 1

ENTRYPOINT ["/entrypoint.sh"]
