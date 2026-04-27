# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — video-sampler (M0 stub)
# -----------------------------------------------------------------------------
FROM alpine:3.20

RUN addgroup -S -g 10001 app && adduser -S -G app -u 10001 -h /app -s /sbin/nologin app
WORKDIR /app

COPY --chown=app:app entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 50053
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/bin/sh", "-c", "test -f /tmp/ready"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
