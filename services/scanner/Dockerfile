# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — scanner (M0 stub)
# -----------------------------------------------------------------------------
# Real image will install nmap and a Python wrapper. M0 stub only.
# -----------------------------------------------------------------------------
FROM alpine:3.20

RUN addgroup -S -g 10001 app && adduser -S -G app -u 10001 -h /app -s /sbin/nologin app
WORKDIR /app

COPY --chown=app:app entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Note: real scanner needs CAP_NET_RAW + CAP_NET_ADMIN; granted via compose, not here.
USER app
HEALTHCHECK --interval=60s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/bin/sh", "-c", "test -f /tmp/ready"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
