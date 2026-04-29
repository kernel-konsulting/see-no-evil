#!/bin/sh
# see-no-evil — caddy entrypoint
#
# Renders the Caddyfile template using envsubst and starts Caddy.
set -eu

SNE_HOSTNAME="${SNE_HOSTNAME:-seenoevil.lan}"
SNE_TLS_MODE="${SNE_TLS_MODE:-internal}"
SNE_TLS_EMAIL="${SNE_TLS_EMAIL:-}"
SNE_API_UPSTREAM="${SNE_API_UPSTREAM:-api:8000}"
SNE_UI_UPSTREAM="${SNE_UI_UPSTREAM:-ui:8081}"

export SNE_HOSTNAME SNE_TLS_MODE SNE_TLS_EMAIL SNE_API_UPSTREAM SNE_UI_UPSTREAM

CADDYFILE="/etc/caddy/Caddyfile"
TEMPLATE="/etc/caddy/Caddyfile.template"

case "${SNE_TLS_MODE}" in
  acme)
    if [ -z "${SNE_TLS_EMAIL}" ]; then
      echo "SNE_TLS_MODE=acme requires SNE_TLS_EMAIL to be set" >&2
      exit 1
    fi
    # Replace 'tls internal' with 'tls <email>' for ACME issuance.
    envsubst '${SNE_HOSTNAME} ${SNE_API_UPSTREAM} ${SNE_UI_UPSTREAM}' < "${TEMPLATE}" \
      | sed "s|tls internal|tls ${SNE_TLS_EMAIL}|" > "${CADDYFILE}"
    ;;
  off)
    # Strip TLS block entirely — plain HTTP on :80.
    envsubst '${SNE_HOSTNAME} ${SNE_API_UPSTREAM} ${SNE_UI_UPSTREAM}' < "${TEMPLATE}" \
      | sed '/^    tls /d' \
      | sed "s|^${SNE_HOSTNAME}, localhost, 127.0.0.1 {|:80 {|" > "${CADDYFILE}"
    ;;
  *)
    # Default: internal CA, no email needed.
    envsubst '${SNE_HOSTNAME} ${SNE_API_UPSTREAM} ${SNE_UI_UPSTREAM}' < "${TEMPLATE}" > "${CADDYFILE}"
    ;;
esac

echo "Starting Caddy for ${SNE_HOSTNAME} (TLS mode: ${SNE_TLS_MODE})"
exec caddy run --config "${CADDYFILE}" --adapter caddyfile
