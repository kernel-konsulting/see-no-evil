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

**M11 review hardening (round 2):**

- XFF trusted proxies now cover Docker/Podman `172.16.0.0/12` + `192.168.0.0/16` and `SEENOEVIL_TRUSTED_PROXIES` override; rate limiting no longer collapses behind Caddy.
- Wizard generates `proxy.api_token` / `scanner.api_token` on first run so `fail_closed:true` fresh installs never brick.
- CSRF double-submit enforced globally via middleware + UI axios `x-csrf-token` header.
- Quarantine `GET`/`flag` now `require_user` (viewer can list + flag false positives); `allow`/`deny`/`delete` remain admin.
- `POST /v1/profiles` and other mutating endpoints require CSRF header (tests use auto-injected header).
- `backup restore` now rejects symlinks and uses `tarfile.data_filter` (Python 3.12+).
- `cleanup_expired` uses naive UTC consistently; `panic` audit rows now HMAC-signed.
- OIDC state bound to browser cookie + `nonce` to prevent fixation.

**M12 review hardening (round 3 — whole-codebase):**

- XFF now uses `ipaddress` CIDR matching, narrow defaults `10.88.0.0/16` + `172.16.0.0/12` + `127.0.0.0/8`, last-value trust, and `forwarded_allow_ips` synced with `SEENOEVIL_TRUSTED_PROXIES`.
- CSRF bearer bypass closed (session cookie present -> still enforce), `Secure` flag via `X-Forwarded-Proto`, kill-switch logs warning.
- OIDC nonce verified against `id_token`, discovery cached with 1h TTL, state deleted only after success, missing cookie now `400`.
- Backup rejects `fifo`/`device` nodes, audit HMAC includes thumbnail hash (with legacy fallback).
- `cleanup` no longer blocks event loop, wizard no longer leaks token prefix, `ImageThresholds` syncs `drawing`/`drawings`.
- Proxy `LeafCache` double-checked locking, canonical host helper, `capped()`/`hasImageMagic()`, quota batch lock + backoff.
- Scanner host-network fallback via `urlparse`, try-lock `429`, fail-closed control plane, updater retry with backoff, rate-limiter sweep.

**M13 review hardening (round 4 — whole-codebase, 39 items):**

- **Filter bypass closed:** `text/*` + `application/xml`/`+xml`/`javascript`/`css` now classified (F02), `shouldInspectWithBody` sniffs `hasImageMagic` on any CT (F07), text caps 16→64 with head+tail sampling (F08), large-image truncation fail-closed (F09).
- **Identity & quota:** `quota_day()` centralizes pod-TZ day boundary (F20), `Device.ip` indexed + auto-create rate-limited 20/hour + IP-collision check (F06), `decide` no longer honors `body.decision=allow` bypass (F03).
- **Deployment:** `SEENOEVIL_CONFIG` now `/data/config.yaml` with fallback to `config.example.yaml` (F04), wizard only generates tokens on new config (F21), CA endpoint fixed `/v1/ca/cert`.
- **Reliability:** `peekBody` preserves tail on error + `inspectionSem` 50 cap (F05/F18), `tunnel` 2m deadline (F11), `backup snapshot` uses sqlite backup API (F13), `cleanup` batches 1000 + aware UTC (F23), `notifications` async via `to_thread` (F30), quota backoff 10m + `rand` jitter per-IP (F25).
- **Auth:** JWT rotated on password change (F14), `ensure_secret`/`_ensure_jwt_secret` catch `IntegrityError` + `GET /v1/audit` read-only (F19), OIDC issuer/https + `token_endpoint` host validation + `Secure` via `_secure_cookies` + single-use state (F15/F17/F26), last-admin patch guard (F35).
- **Remaining polish:** quarantine bulk returns `skipped_expired` (F22), settings patch type-checked (F24), scanner CIDR via `ip_network` strict=False (F28), video zero-frames → `block` (F29), SafeSearch `google.` TLD (F36), hard caps doc sync (F32), policy.proto deprecated note (F33).

**Deferred to follow-up M1 PRs:**

- OPA / rego integration (the in-tree policy engine has the same interface,
  so the swap is local to `policy.py`).
- WebAuthn (config knobs are present and validated; not consumed).

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
The container expects it mounted at `/data/config.yaml` (fallback to
`/etc/seenoevil/config.example.yaml` before wizard); point
`SEENOEVIL_CONFIG` at it. `proxy.fail_closed` defaults `true` (fail-closed)
when absent; set `false` to revert to fail-open.
**M13/M14 hardening:** `quota_day()` pod-TZ, fail-closed defaults, JWT
rotation, OIDC host validation, backup sqlite hot-copy, tunnel SSRF denylist,
text head+tail caps, video no_frames fail-closed-aware, cleanup naive UTC
batches, peekBody drain, quota jitter crypto-rand, inspection 10s budget.

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
