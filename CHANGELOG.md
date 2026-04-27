# Changelog

All notable changes to **see-no-evil** are recorded here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) loosely; versions
follow [SemVer](https://semver.org/) once we hit `1.0.0`.

## [0.1.0] — 2026-04-27

First public release candidate. Every milestone in PLAN.md (M0–M9) is
implemented.

### Added

- **DNS** — Blocky-based resolver with per-client groups (M1.4)
- **MITM proxy** — Go data-plane with auto-generated CA, SNI bypass list,
  SafeSearch enforcement, YouTube cookie injection, gRPC fan-out to
  classifiers (M1.3)
- **Image classifier** — Python + ONNX, Freepik (default) and Falconsai
  models, gRPC :50051 (M1.2)
- **Text classifier** — Python + ONNX, gRPC :50052 (M1.2, body inspection
  in M4)
- **Video sampler** — Go + ffmpeg, keyframe sampling (M5)
- **Control-plane API** — FastAPI + SQLAlchemy 2 + Alembic, SQLite default,
  Postgres opt-in. Endpoints for auth, profiles, devices, audit, decide,
  quarantine, quota, panic, alerts (M1.1, M3, M6, M9)
- **Admin UI** — React + Vite + TypeScript + shadcn/ui (M1.4, M3, M7)
- **Caddy reverse proxy** with internal CA / ACME (M1.4)
- **Install wizard** — `seenoevil-setup` CLI, compose `setup` profile (M1.5)
- **Scanner** — nmap + mDNS + SSDP discovery, IP/vendor surfaced in UI (M1.6,
  M7)
- **Updater** — pulls models, blocklists, OUI DB on first start (M1.2)
- **Schedules + quotas + panic-relax** with ntfy / webhook notifications
  (M6)
- **OIDC sign-in** — Authorization Code + PKCE, email allow-list (M8)
- **Backup CLI** — `seenoevil-backup snapshot|list|restore` plus optional
  `backup` and `litestream` compose profiles (M8)
- **VPN profiles** — Tailscale and wg-easy sidecars (M8)
- **Observability** — Vector + VictoriaMetrics + vmalert + Grafana with
  three dashboards and three alert groups; alerts routed back through the
  notifications fan-out (M9)

### Known limitations

- OPA Rego engine is scaffolded under `policies/` but the in-tree Python
  policy engine is still authoritative; Go embed is deferred.
- WebAuthn config is validated but not consumed.
- Audit retention rotation is manual.
- `tsconfig.json` for the UI uses `ignoreDeprecations: "6.0"`, which CI
  TypeScript >= 5.7 rejects; pinned for upstream fix.

### Security

- License: PolyForm-Noncommercial-1.0.0 — commercial use requires a paid
  license.
- Default-deny egress on classifier and proxy containers (compose
  `internal: true` network).
- MITM CA stored under `/data/ca/`; private key encrypted with a passphrase
  set at install time.
- Admin auth uses Argon2id; sessions are HS256 cookies.
- See [SECURITY.md](SECURITY.md) for vulnerability reporting.
