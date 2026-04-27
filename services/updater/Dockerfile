# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — updater
#
# Runs once at startup (and on a cron schedule) to fetch model weights and
# blocklists into the /data named volume.  Has egress; classifiers do not.
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY services/updater/pyproject.toml ./
COPY services/updater/src/ ./src/
RUN pip install --upgrade pip && pip install --prefix=/install "."

# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/lib/python3.12/site-packages

RUN groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --home /app --shell /sbin/nologin app

WORKDIR /app
COPY --from=builder /install /app

USER app

HEALTHCHECK --interval=60s --timeout=5s --start-period=5s --retries=3 \
    CMD ["/bin/sh", "-c", "test -f /data/models/.updated"]

CMD ["python", "-m", "seenoevil_updater"]
