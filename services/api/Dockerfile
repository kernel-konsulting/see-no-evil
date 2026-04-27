# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — api (M0 stub)
# -----------------------------------------------------------------------------
# Real source lands in M1. For now this image just exits 0 on a healthcheck so
# the compose file can be exercised end-to-end.
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Non-root user
RUN groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --home /app --shell /sbin/nologin app

# Placeholder entrypoint — replaced in M1 with `uvicorn seenoevil.api:app`.
COPY --chown=app:app entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["/bin/sh", "-c", "test -f /tmp/ready"]

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
