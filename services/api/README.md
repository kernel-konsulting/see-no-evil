# API (control plane)

FastAPI + SQLAlchemy + Alembic. Hosts the admin REST API, embeds the OPA
policy engine, and is fronted by Caddy (or your own reverse proxy).

**M0:** Dockerfile stub only. No source yet.

## Responsibilities

- CRUD for profiles, devices, allow/deny lists.
- Authentication (built-in admin + optional OIDC + WebAuthn).
- Policy decisions (delegated to embedded OPA).
- Audit log persistence and rotation.
- Quota tracking (minutes-of-use per profile per day).
- Coordinating updater runs.

## Endpoints (planned)

- `GET  /healthz`              liveness
- `GET  /readyz`               readiness
- `GET  /metrics`              Prometheus
- `POST /v1/decide`            policy decision (called by proxy)
- `GET  /v1/profiles`          ...
- `*    /v1/devices`           ...
- `*    /v1/audit`             ...
