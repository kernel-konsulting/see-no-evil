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
FROM ghcr.io/0xerr0r/blocky:v0.24 AS runtime

COPY --from=downloader /lists/ /data/lists/
COPY services/dns/blocky.yaml /etc/blocky/blocky.yaml
COPY --chmod=0755 services/dns/entrypoint.sh /entrypoint.sh

EXPOSE 53/udp 53/tcp
# REST API (for flushing cache, query API)
EXPOSE 4000
# Prometheus metrics
EXPOSE 4001

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD wget -qO- http://localhost:4000/api/blocking/status || exit 1

ENTRYPOINT ["/entrypoint.sh"]
