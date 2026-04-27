# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — image-classifier (M0 stub)
# -----------------------------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --home /app --shell /sbin/nologin app

COPY --chown=app:app entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 50051
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["/bin/sh", "-c", "test -f /tmp/ready"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
