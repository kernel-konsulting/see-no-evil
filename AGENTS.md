# see-no-evil — Agent Instructions

This file is read by GitHub Copilot, Claude Code, and any other AI coding agent
working in this repository. Follow these instructions for every task.

---

## Project overview

**see-no-evil** is a self-hosted parental-control / content-filtering pod for home networks.
It inspects HTTP/HTTPS traffic via a MITM proxy backed by ONNX ML classifiers running
fully offline. Target hardware: Raspberry Pi 4/5, N100 mini-PC, or any ARM/x86 Linux host.

**License:** PolyForm-Noncommercial-1.0.0. Commercial use requires a separate license.

---

## Non-negotiable rules before marking any work complete

> **These apply to every PR, every commit, every "done" claim.**

1. **Run all tests** that touch changed code:
   - Python services: `pytest` in the service directory
   - Go proxy: `go test ./...` from `services/proxy/`
   - UI: `npm run test` from `services/ui/`

2. **Run all linters**:
   - Python: `ruff check . && ruff format --check .`
   - Go: `golangci-lint run ./...` (after `make generate`)
   - TypeScript/JS: `npm run lint`
   - YAML: `yamllint .`
   - Rego (when policies/ has files): `opa fmt --diff . && opa test -v .`

3. **Update `PLAN.md`**: Mark the milestone row ✅ and update any open items.

4. **Update the service `README.md`**: Document what is now implemented vs. still stubbed.

5. **Validate compose**: If any service definition changed, run `docker compose -f deploy/compose/docker-compose.yml config` and confirm it exits cleanly.

---

## Architecture at a glance

```
LAN → DNS (Blocky, port 53)
LAN → MITM Proxy (Go, port 8080)
          │
          ├─gRPC→ image-classifier (Python+ONNX, :50051)
          ├─gRPC→ text-classifier  (Python+ONNX, :50052)
          └─HTTP→ API (Python FastAPI, :8000) ← /v1/decide
                      │
                      └── SQLite (default) or Postgres (opt-in)
Admin browser → Caddy (:80/:443) → UI (React/Vite) → API
```

Two TLS worlds — **never confuse them**:
- **Admin UI** TLS: terminated by Caddy, Caddy's own internal CA or Let's Encrypt.
- **Client traffic MITM** TLS: terminated by the Go proxy using a device-trusted see-no-evil CA.

---

## Service directory map

| Directory | Language | Status |
|---|---|---|
| `services/api/` | Python FastAPI | ✅ M1.1/M8 |
| `services/proxy/` | Go | ✅ M1.3 |
| `services/image-classifier/` | Python + ONNX | ✅ M1.2 |
| `services/text-classifier/` | Python + ONNX | ✅ M1.2 |
| `services/updater/` | Python | ✅ M1.2 |
| `services/dns/` | Blocky config | ✅ M1.4 |
| `services/ui/` | React + Vite + TS | ✅ M1.4 |
| `services/video-sampler/` | Go + ffmpeg | ✅ M5 |
| `services/scanner/` | Python + nmap | ✅ M1.6/M7 |
| `services/notifier/` | Python | ✅ M6 (via api) |
| `services/cert-helper/` | Python | ✅ M1.5 (api ca router) |
| `shared/proto/` | Protobuf | ✅ M1.2 |

---

## Build cheatsheet

```bash
# Python service
cd services/<name>
pip install -e ".[test]"
pytest && ruff check . && ruff format --check .

# Go proxy (generate stubs first)
cd services/proxy
make generate && go build ./... && go test ./...

# UI
cd services/ui
npm install && npm run build && npm run test && npm run lint

# Compose sanity check
docker compose -f deploy/compose/docker-compose.yml config
```

---

## Coding conventions

- **Python**: ruff enforced via `ruff.toml` at root. `pyproject.toml`-only (no `setup.py`).
- **Go**: `gofmt` + golangci-lint. Module `github.com/kernel-konsulting/see-no-evil/services/proxy`.
- **TypeScript**: strict mode, no `any`, Prettier + ESLint. React functional components only.
- **gRPC stubs**: generated at build/CI time from `shared/proto/*.proto`. Never hand-edit `*_pb2*.py` or `*.pb.go`.
- **Secrets**: env vars or Docker secrets only. Never committed.
- **Egress policy**: classifier and proxy containers must have no internet egress. Only `updater`, `notifier`, and DNS containers may reach external hosts.
- **No baked weights**: models fetched by `updater` at first start, cached to `/data/models/`.

---

## Key configuration

Single user-facing file: `config.yaml` (see `config.example.yaml` for the full shape).
Services read their slice; unknown keys are allowed (other services may own them).

Key env vars:

| Variable | Service | Purpose |
|---|---|---|
| `SEENOEVIL_CONFIG` / `CONFIG_PATH` | all | Path to config.yaml |
| `SEENOEVIL_INITIAL_ADMIN_PASSWORD` | api | Bootstrap admin on first start |
| `IMAGE_CLASSIFIER_ADDR` | proxy | gRPC target (default `image-classifier:50051`) |
| `TEXT_CLASSIFIER_ADDR` | proxy | gRPC target (default `text-classifier:50052`) |
| `API_ADDR` | proxy | Policy API HTTP base URL (default `api:8000`) |

---

## Open milestones (see PLAN.md for full detail)

| ID | Scope |
|---|---|
| M2 | OPA wiring — `policies/seenoevil.rego` is CI-tested but `/v1/decide` runs the Python engine; wire OPA in or remove the rego |
| M9 | Opt-in `backup` / `litestream` compose profiles ship commented; validate before enabling |
| — | `vpn-wg` (wg-easy) profile: decide and document (Tailscale ships enabled) |
