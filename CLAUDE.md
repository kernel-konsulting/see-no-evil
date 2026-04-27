# see-no-evil — Claude Code Instructions

## Project overview

see-no-evil is a self-hosted parental-control / content-filtering pod for home networks.
It runs on ARM + x86 (Pi 4/5, N100, workstation) and inspects HTTP/HTTPS traffic using a
MITM proxy backed by ONNX machine-learning classifiers. Everything classified stays on the
box; no inspected content ever leaves.

License: **PolyForm-Noncommercial-1.0.0** — commercial use requires a paid license.

---

## Stack

| Layer | Technology |
|---|---|
| DNS | Blocky (Go binary, per-client groups) |
| Proxy data-plane | Go — `elazarl/goproxy`-style MITM, gRPC fan-out to classifiers |
| Image classifier | Python FastAPI + ONNX Runtime (Freepik / Falconsai model) — gRPC port 50051 |
| Text classifier | Python FastAPI + ONNX Runtime (michellejieli NSFW) — gRPC port 50052 |
| Video sampler | Go + ffmpeg (keyframe sampling → image-classifier) |
| Control-plane API | Python FastAPI + SQLAlchemy 2 + Alembic (SQLite default, Postgres opt-in) |
| Admin UI | React 18 + Vite + TypeScript + shadcn/ui + Tailwind + React Query |
| Admin reverse proxy | Caddy (internal CA / ACME — separate from MITM CA) |
| Policy engine | Python rule engine now; OPA (Go embed) in M2 |
| Auth | Argon2id + optional TOTP + OIDC (pluggable) |
| Cache | In-process default; Redis opt-in via `cache.url` |
| Logs | Vector → rotating SQLite/Parquet |
| Orchestration | Docker Compose (profiles) / podman play kube / Helm |

---

## Repository layout

```
services/
  api/            Python FastAPI control-plane (M1.1 ✅)
  proxy/          Go MITM data-plane (M1.3 ✅)
  image-classifier/ Python gRPC + ONNX (M1.2 ✅)
  text-classifier/  Python gRPC + ONNX (M1.2 ✅)
  updater/        Python — first-start model/list fetcher (M1.2 ✅)
  dns/            Blocky config + rendered lists (M1.4)
  ui/             React + Vite + TypeScript (M1.4)
  video-sampler/  Go + ffmpeg (M5)
  scanner/        Python nmap wrapper (M1.6 / M7)
  notifier/       Python ntfy/webhook fan-out (M6)
  cert-helper/    Python CA install wizards (M1.5)
shared/
  proto/          classify.proto, policy.proto (M1.2 ✅)
  schema/         JSON Schema for config & policy
deploy/
  compose/        docker-compose.yml with profiles
  podman/         podman play kube YAML
  helm/           Helm chart
policies/         Rego sources + opa tests (M2)
PLAN.md           Internal implementation plan (git-ignored)
```

---

## Build commands

### Python services (api, image-classifier, text-classifier, updater)
```bash
cd services/<service>
pip install -e ".[test]"
pytest                      # run tests
ruff check .                # lint
ruff format --check .       # format check
```

### Go proxy
```bash
cd services/proxy
make generate               # run protoc to generate gRPC stubs
go build ./...
go test ./...
golangci-lint run ./...
```

### UI
```bash
cd services/ui
npm install
npm run build
npm run test
npm run lint
```

### Compose (local dev)
```bash
docker compose --profile core up -d   # from deploy/compose/
docker compose --profile core --profile postgres up -d
```

---

## Before marking any task complete

**This is mandatory — do not skip any step.**

1. **Run relevant tests:**
   - Python: `pytest` in the affected service directory
   - Go: `go test ./...` in `services/proxy`
   - UI: `npm run test` in `services/ui`

2. **Run linters:**
   - Python: `ruff check . && ruff format --check .`
   - Go: `golangci-lint run ./...`
   - UI: `npm run lint`
   - YAML: `yamllint .`
   - Rego: `opa fmt --diff . && opa test -v .` (when policies/ has files)

3. **Update PLAN.md:** Mark the completed milestone as ✅ and update any open items.

4. **Update service README.md:** Document what changed vs. what is still stubbed.

5. **Check docker-compose.yml:** If a new service was added or changed, update the compose file and verify `docker compose config` validates cleanly.

---

## Coding conventions

- **Python**: ruff enforced (see `ruff.toml` at root). All services use `pyproject.toml`; no `setup.py`.
- **Go**: standard `gofmt` + golangci-lint. Module path `github.com/kernel-konsulting/see-no-evil/services/proxy`.
- **TypeScript**: strict mode, no `any`, Prettier + ESLint.
- **Imports**: no relative imports crossing service boundaries — services talk via gRPC or HTTP.
- **Secrets**: never commit real credentials. Use env vars or Docker secrets.
- **Security**: follow OWASP Top 10. Validate all inputs at service boundaries. Classifiers and proxy containers must have no internet egress.
- **No baked-in model weights**: models are fetched by `updater` on first start and cached to `/data/models/`.

---

## Two TLS worlds — never confuse them

| Surface | Terminated by | Cert issued by |
|---|---|---|
| Admin UI (`https://seenoevil.lan`) | Caddy | Caddy internal CA / Let's Encrypt / BYO |
| Client traffic (MITM inspection) | Go proxy | see-no-evil MITM CA (installed on devices) |

---

## gRPC contracts

Generated from `shared/proto/classify.proto`. Regenerate stubs with:
- **Go**: `cd services/proxy && make generate`
- **Python**: `python -m grpc_tools.protoc -I shared/proto --python_out=... --grpc_python_out=... shared/proto/classify.proto`

Never edit generated `*_pb2*.py` or `*.pb.go` files by hand.

---

## Key environment variables

| Variable | Service | Description |
|---|---|---|
| `CONFIG_PATH` | all | Path to config.yaml (default `/data/config.yaml`) |
| `SEENOEVIL_CONFIG` | api | Same, Python spelling |
| `SEENOEVIL_INITIAL_ADMIN_PASSWORD` | api | Seeds admin on first start |
| `IMAGE_CLASSIFIER_MODEL_PATH` | image-classifier | Path to ONNX model |
| `TEXT_CLASSIFIER_MODEL_PATH` | text-classifier | Path to ONNX model |
| `IMAGE_CLASSIFIER_ADDR` | proxy | gRPC address (default `image-classifier:50051`) |
| `TEXT_CLASSIFIER_ADDR` | proxy | gRPC address (default `text-classifier:50052`) |
| `API_ADDR` | proxy | Policy API address (default `api:8000`) |
| `PROXY_CA_PASSPHRASE` | proxy | Passphrase for auto-generated CA key |

---

## Milestones (reference PLAN.md for full detail)

| ID | Scope | Status |
|---|---|---|
| M0 | Skeleton, LICENSE, CI, stub Dockerfiles | ✅ |
| M1.1 | API + config + DB + policy engine | ✅ |
| M1.2 | gRPC contracts + classifiers + updater | ✅ |
| M1.3 | Go MITM proxy data-plane | ✅ |
| M1.4 | DNS/Blocky + UI shell + Caddy | 🔄 |
| M1.5 | First-run wizard + compose healthchecks | 🔄 |
| M2–M9 | See PLAN.md | ⬜ |
