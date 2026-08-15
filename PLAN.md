# PLAN — milestone status

> Source of truth for what is implemented vs. still open. Update the status
> column whenever a milestone lands (AGENTS.md requires this for every PR).

## Status legend

- ✅ **done** — implemented, tested, and wired into the pod.
- 🟡 **partial** — core is done; a tracked sub-item remains.
- ⬜ **open** — not started.

## Milestones

| ID | Scope | Status |
|---|---|---|
| M0 | Repo skeleton, threat model, CI (lint/test/opa/buildx) | ✅ done |
| M1.1 | Control-plane API: FastAPI + SQLite/Postgres, profiles, devices, audit, quarantine, RBAC users, OIDC, panic-relax, settings, backup | ✅ done |
| M1.2 | Image + text classifiers (ONNX, gRPC), updater with checksum-verified model catalogue, shared protos | ✅ done |
| M1.3 | Go MITM proxy: CONNECT MITM, per-host leaf CA, SafeSearch, text strip/block, video sampling, YouTube thumbnail blocking, quota heartbeat reporter, fail-closed option, IP-based device attribution | ✅ done |
| M1.4 | DNS (Blocky wrapper + blocklist updater), admin UI shell, Caddy ingress | ✅ done |
| M1.5 | First-run install wizard (`sne-setup`), compose healthchecks, CA distribution endpoints | ✅ done |
| M1.6 | Scanner (nmap discovery, token-authenticated), observability profile (Vector + VictoriaMetrics + Grafana + vmalert + cAdvisor) | ✅ done |
| M2 | URL/keyword filter, SafeSearch polish, YouTube channel allow/deny | ✅ done |
| M2 | OPA policy engine wiring — `policies/seenoevil.rego` is CI-tested but the API still runs the Python engine; wire OPA in or remove the rego | 🟡 partial |
| M3 | Quarantine queue (allow/deny/flag/bulk, blurred previews) | ✅ done |
| M4 | Text inspection modes (`off` / `block` / `strip`) | ✅ done |
| M5 | Video sampler (ffmpeg frame extraction, worst-frame verdict, concurrency-bounded) | ✅ done |
| M6 | Notifications (ntfy/webhook fan-out for blocks + vmalert alerts), panic-relax | ✅ done |
| M7 | Device auto-discovery (scanner + proxy-driven), vendor enrichment, synthetic MACs from IP | ✅ done |
| M8 | Multi-user RBAC, OIDC, audit HMAC tamper detection, retention cleanup | ✅ done |
| M9 | Backup/restore + Litestream replication profiles, observability dashboards, docs polish | 🟡 partial — `backup` / `litestream` compose profiles ship commented (opt-in) |

## Open sub-items (tracked)

- [ ] Wire OPA (`policies/seenoevil.rego`) into `/v1/decide`, or delete the rego (keep one policy engine).
- [ ] Uncomment + validate the `backup` / `litestream` compose profiles.
- [ ] `vpn-wg` (wg-easy) profile: decide and document; Tailscale profile ships enabled.
