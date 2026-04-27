#!/bin/sh
# see-no-evil — caddy entrypoint
#
# Renders the Caddyfile template using envsubst and starts Caddy.
set -eu

SNE_HOSTNAME="${SNE_HOSTNAME:-seenoevil.lan}"
SNE_TLS_MODE="${SNE_TLS_MODE:-internal}"
SNE_TLS_EMAIL="${SNE_TLS_EMAIL:-}"

export SNE_HOSTNAME SNE_TLS_MODE SNE_TLS_EMAIL

CADDYFILE="/etc/caddy/Caddyfile"
TEMPLATE="/etc/caddy/Caddyfile.template"

case "${SNE_TLS_MODE}" in
  acme)
    # Replace the tls block with ACME / Let's Encrypt config.
    envsubst '${SNE_HOSTNAME} ${SNE_TLS_EMAIL}' < "${TEMPLATE}" \
      | sed 's/issuer internal//' > "${CADDYFILE}"
    ;;
  off)
    # Strip TLS block entirely — plain HTTP.
    envsubst '${SNE_HOSTNAME}' < "${TEMPLATE}" \
      | sed '/^    tls/,/^    }/d' \
      | sed "s/${SNE_HOSTNAME}/:80/" > "${CADDYFILE}"
    ;;
  *)
    # Default: internal CA.
    envsubst '${SNE_HOSTNAME} ${SNE_TLS_EMAIL}' < "${TEMPLATE}" > "${CADDYFILE}"
    ;;
esac

echo "Starting Caddy for ${SNE_HOSTNAME} (TLS mode: ${SNE_TLS_MODE})"
exec caddy run --config "${CADDYFILE}" --adapter caddyfile
