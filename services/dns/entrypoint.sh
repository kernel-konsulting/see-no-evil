#!/bin/sh
# see-no-evil — dns entrypoint
#
# Ensures required list files exist (so Blocky doesn't fail on missing paths)
# then starts Blocky with the baked-in config.
set -eu

DATA_DIR="${DATA_DIR:-/data}"
LISTS_DIR="${DATA_DIR}/lists"
CONFIG="${BLOCKY_CONFIG:-/etc/blocky/blocky.yaml}"

# Ensure list files exist so Blocky doesn't bail on missing paths.
mkdir -p "${LISTS_DIR}"
for f in custom_allow.txt custom_deny.txt; do
    [ -f "${LISTS_DIR}/${f}" ] || touch "${LISTS_DIR}/${f}"
done

exec /app/blocky --config "${CONFIG}"
