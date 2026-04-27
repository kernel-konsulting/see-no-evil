# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — api (M1.1)
# -----------------------------------------------------------------------------
# FastAPI control-plane. Multi-stage build to keep the runtime image small and
# free of build toolchains.
# -----------------------------------------------------------------------------

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Build deps for argon2-cffi and (optional) psycopg.
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential libffi-dev libpq-dev \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src

# Install into an isolated prefix that we'll copy verbatim into the runtime image.
RUN pip install --prefix=/install ".[postgres]"


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Minimal runtime libs: libpq for psycopg, libffi for argon2.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libpq5 libffi8 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --home /app --shell /sbin/nologin app \
 && mkdir -p /data /etc/seenoevil \
 && chown -R app:app /data /etc/seenoevil

COPY --from=builder /install /usr/local

WORKDIR /app
USER app

ENV SEENOEVIL_API_HOST=0.0.0.0 \
    SEENOEVIL_API_PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3).status==200 else 1)"]

ENTRYPOINT ["seenoevil-api"]
