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
- Block reasons name the matched rule where possible, e.g.
  `deny_domain:tiktok.com`, `deny_keyword:casino`, or
  `classifier:image:porn`.
- Endpoints:
  - `GET  /healthz`
  - `GET  /readyz`
  - `GET  /metrics` (Prometheus)
  - `POST /v1/auth/setup` (only valid before any admin exists)
  - `POST /v1/auth/login`
  - `POST /v1/auth/logout`
  - `GET/POST/PATCH/DELETE /v1/profiles[/{id}]` (admin)
  - `GET/POST/PATCH/DELETE /v1/devices[/{id}]` (admin)
  - `POST /v1/devices/discover` (M1.6 — scanner upserts MAC list)
  - `GET/DELETE /v1/audit` (admin; list or clear audit entries)
  - `POST /v1/decide` (called by the proxy)
  - `GET/POST/DELETE /v1/quarantine[/{id}[/allow|/deny]]` (admin; M3 quarantine queue)

**M2 additions:**

- URL keyword filter (`profiles.*.deny.url_keywords`).
- YouTube channel allow/deny rules with handle/ID extraction.
- Profile and global allow domains are explicit allow overrides by default.
  Default-deny allowlist mode is available only through the advanced
  `enforce_allowlist` / `enforce_global_allowlist` switches.

**M3 additions:**

- `QuarantineItem` model + migration `0003_quarantine`.
- `/v1/decide` auto-creates a pending quarantine entry when an image/video
  response is blocked by the classifier (other block reasons stay out of the
  queue to avoid noise).
- `POST /v1/quarantine/{id}/allow` and `/deny` resolve items.

**M6 additions:**

- Panic-relax mode persisted in `settings["panic_relax"]`. When active the
  decide engine short-circuits to `allow` with reason `panic_relax`. State
  changes write an `AuditDecision` row.
  - `GET    /v1/admin/panic` — current state (admin)
  - `POST   /v1/admin/panic` — `{duration_minutes, reason}` (admin)
  - `DELETE /v1/admin/panic` — disable now (admin)
- Quota heartbeat — proxy/agents POST minutes of active use; the row in
  `quotas` is upserted for today and read by the decide engine.
  - `POST   /v1/quota/heartbeat` — `{device_mac|device_id, minutes}`
  - `GET    /v1/quota/{device_id}` — admin view
  - `DELETE /v1/quota/{device_id}` — admin reset
- Outbound notifications (best-effort, BackgroundTask) for block events on
  profiles with `notify_on_block = true`, plus panic enable/disable. Configured
  under `notifications:` in `config.yaml` (`ntfy_url`, `webhook_url`,
  `webhook_token`). When unset, no HTTP is performed.

**M7 additions:**

- `Device` table gained `ip` and `vendor` columns (migration `0004_device_enrichment`).
  `POST /v1/devices/discover` now stores both. IP is refreshed on every scan
  (DHCP leases change); vendor is preserved once set so admin edits aren't
  clobbered. The Devices page in the UI renders both columns plus a "New"
  badge for devices created in the last 7 days.

**M8 additions:**

- OIDC sign-in (Authorization Code + PKCE; ~200 lines, no Authlib dep).
  - `GET /v1/auth/oidc/start` — returns `{authorize_url, state}`; PKCE
    verifier + state stashed in the `settings` table for 10 minutes.
  - `GET /v1/auth/oidc/callback?code=...&state=...` — exchanges the code,
    fetches `email` from the IdP's `userinfo` endpoint, validates against
    `auth.oidc.allowed_emails`, then issues the standard session cookie
    and redirects to `/`.
  - Returns 404 when `auth.oidc.enabled` is false. See [docs/oidc.md](../../docs/oidc.md).
- `seenoevil-backup` CLI: `snapshot` / `list` / `restore` over a tarball of
  the SQLite DB + CA + cached models. `backup.local_path` and
  `backup.retention` are honoured. See [docs/backup.md](../../docs/backup.md).
- `litestream` config block (validated only) for the optional Litestream
  sidecar that streams `policy.db` to S3-compatible storage. Wired up via
  the `litestream` compose profile + `services/api/litestream.yml`.

**M9 additions:**

- `POST /v1/alerts/webhook` — receives vmalert payloads and fans them out
  through the same `notifications:` config (ntfy + webhook) used for block
  events. Severity / alertname / summary land in the notification body.
- Observability profile (Vector + VictoriaMetrics + vmalert + Grafana +
  cAdvisor) ships three dashboards (overview / classifiers / host) and
  three alert groups (availability / quality / backup). See
  [services/observability/README.md](../observability/README.md).

**Deferred to follow-up M1 PRs:**

- OPA / rego integration (the in-tree policy engine has the same interface,
  so the swap is local to `policy.py`).
- WebAuthn (config knobs are present and validated; not consumed).
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
