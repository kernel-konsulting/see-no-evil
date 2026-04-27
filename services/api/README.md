# API (control plane)

FastAPI + SQLAlchemy 2.x + Alembic. Hosts the admin REST API and (eventually)
the embedded OPA policy engine. Fronted by Caddy in production; talks to
SQLite (default) or PostgreSQL via the same `db.url`.

## Status

**M1.1 implemented:**

- Pydantic config schema mirroring `config.example.yaml`, validated at startup.
- SQLAlchemy models: `Profile`, `Device`, `AuditDecision`, `Quota`, `Setting`.
- Alembic baseline migration (works on SQLite and PostgreSQL via batch mode).
- FastAPI app with a lifespan that runs migrations and seeds `profiles[]` from
  config on first start.
- Built-in admin auth: argon2 password + HS256 session cookie.
- Minimal Python policy engine (URL allow/deny + classifier thresholds +
  schedule windows + daily quota).
- Endpoints:
  - `GET  /healthz`
  - `GET  /readyz`
  - `GET  /metrics` (Prometheus)
  - `POST /v1/auth/setup` (only valid before any admin exists)
  - `POST /v1/auth/login`
  - `POST /v1/auth/logout`
  - `GET/POST/PATCH/DELETE /v1/profiles[/{id}]`
  - `GET/POST/PATCH/DELETE /v1/devices[/{id}]`
  - `POST /v1/devices/discover` (M1.6 — scanner upserts MAC list)
  - `GET  /v1/audit`
  - `POST /v1/decide` (called by the proxy)
  - `GET/POST/DELETE /v1/quarantine[/{id}[/allow|/deny]]` (M3 — quarantine queue)

**M2 additions:**

- URL keyword filter (`profiles.*.deny.url_keywords`).
- YouTube channel allow/deny rules with handle/ID extraction.

**M3 additions:**

- `QuarantineItem` model + migration `0003_quarantine`.
- `/v1/decide` auto-creates a pending quarantine entry when an image/video
  response is blocked by the classifier (other block reasons stay out of the
  queue to avoid noise).
- `POST /v1/quarantine/{id}/allow` and `/deny` resolve items.

**Deferred to follow-up M1 PRs:**

- OPA / rego integration (the in-tree policy engine has the same interface,
  so the swap is local to `policy.py`).
- OIDC + WebAuthn (config knobs are present and validated; not consumed).
- Audit retention rotation worker.

## Local development

```bash
cd services/api
python -m venv .venv && . .venv/bin/activate
pip install -e ".[test,postgres]"

# Optional: set initial admin (otherwise use POST /v1/auth/setup)
export SEENOEVIL_INITIAL_ADMIN_PASSWORD='change-me-please'
export SEENOEVIL_CONFIG=../../config.example.yaml

seenoevil-api
# -> http://127.0.0.1:8000/healthz
```

## Tests

```bash
cd services/api
pytest
```

Uses an ephemeral file-backed SQLite DB per test fixture; the FastAPI app is
constructed via `create_app(AppConfig(...))` so no env vars or YAML files are
required.

## Configuration surface

All knobs come from the repo-root `config.yaml` (see `config.example.yaml`).
The container expects it mounted at `/etc/seenoevil/config.yaml`; point
`SEENOEVIL_CONFIG` at it.

## Database

Schema migrations live in `src/seenoevil_api/alembic/versions/`. The lifespan
runs `alembic upgrade head` on every start, so plain `docker compose up` is
sufficient to bring a fresh deployment online.

To apply migrations manually:

```bash
SEENOEVIL_CONFIG=/path/to/config.yaml \
  python -c 'from seenoevil_api.config import load_config; \
             from seenoevil_api.migrations import upgrade_to_head; \
             upgrade_to_head(load_config().db.url)'
```
