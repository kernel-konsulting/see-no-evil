# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — dns
#
# Wraps the upstream Blocky DNS filter binary with:
#   - A startup script that renders blocky.yaml from config.yaml
#   - Baked-in default NSFW blocklists (StevenBlack, OISD)
#     downloaded at image build time
#   - Runtime lists updated by the updater service into /data/lists/
# -----------------------------------------------------------------------------
FROM alpine:3.20 AS downloader

# Fetch the default blocklists at image build time so the container can start
# fully offline.  The updater keeps them fresh on a schedule.
RUN apk add --no-cache curl ca-certificates \
 && mkdir -p /lists \
 && curl -fsSL -o /lists/stevenblack.txt \
      "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/fakenews-gambling-porn/hosts" \
 && curl -fsSL -o /lists/oisd_nsfw.txt \
      "https://nsfw.oisd.nl/domainswild" \
 || true  # non-fatal if network unavailable at build time; updater will refresh

# -----------------------------------------------------------------------------
FROM ghcr.io/0xerr0r/blocky:v0.24 AS upstream

# -----------------------------------------------------------------------------
FROM alpine:3.20 AS runtime

# Blocky upstream ships as a distroless image (no /bin/sh), which prevents
# us from running our entrypoint wrapper.  Re-package the static binary into
# an alpine base so we have a shell + wget available for the healthcheck.
RUN apk add --no-cache ca-certificates wget tini \
 && addgroup -S blocky && adduser -S -G blocky -u 100 blocky

COPY --from=upstream /app/blocky /app/blocky
COPY --from=downloader /lists/ /data/lists/
COPY services/dns/blocky.yaml /etc/blocky/blocky.yaml
COPY --chmod=0755 services/dns/entrypoint.sh /entrypoint.sh

# Pre-create the writable list files and the data dir so the image works
# even when /data is bind/volume mounted (Podman/Docker copy initial contents
# from the image into a fresh named volume).
RUN touch /data/lists/custom_allow.txt /data/lists/custom_deny.txt \
 && chown -R blocky:blocky /data /etc/blocky

USER blocky

EXPOSE 53/udp 53/tcp
# REST API (for flushing cache, query API)
EXPOSE 4000
# Prometheus metrics
EXPOSE 4001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://127.0.0.1:4000/api/blocking/status || exit 1

ENTRYPOINT ["/sbin/tini", "--", "/entrypoint.sh"]
