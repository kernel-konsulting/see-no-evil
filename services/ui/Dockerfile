# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — ui (M0 stub)
# -----------------------------------------------------------------------------
FROM node:20-alpine

WORKDIR /app
RUN addgroup -S -g 10001 app && adduser -S -G app -u 10001 -h /app -s /sbin/nologin app

COPY --chown=app:app entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 5173
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/bin/sh", "-c", "test -f /tmp/ready"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
