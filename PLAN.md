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
| M2.1 | OPA policy engine wiring — `policies/seenoevil.rego` is CI-tested but the API still runs the Python engine; wire OPA in or remove the rego | ✅ done — OPA sidecar `openpolicyagent/opa:0.68.0-static` on `internal`, `policy.engine: python\|opa\|auto` (default `python`), parity harness 34 cases, `podman --profile core --profile opa` validated |
| M3 | Quarantine queue (allow/deny/flag/bulk, blurred previews) | ✅ done |
| M4 | Text inspection modes (`off` / `block` / `strip`) | ✅ done |
| M5 | Video sampler (ffmpeg frame extraction, worst-frame verdict, concurrency-bounded) | ✅ done |
| M6 | Notifications (ntfy/webhook fan-out for blocks + vmalert alerts), panic-relax | ✅ done |
| M7 | Device auto-discovery (scanner + proxy-driven), vendor enrichment, synthetic MACs from IP | ✅ done |
| M8 | Multi-user RBAC, OIDC, audit HMAC tamper detection, retention cleanup | ✅ done |
| M9 | Backup/restore + Litestream replication profiles, observability dashboards, docs polish | 🟡 partial — `backup` / `litestream` compose profiles ship commented (opt-in) |
| M10 | Whole-codebase review hardening (P0/P1/P2) — bypass/identity/quota/resource/contract fixes, Podman-native | ✅ done — see review findings G1-G6 (59 items). Transparent-mode docs + podman compose validated. |
| M11 | Whole-codebase review round 2 — XFF trust, token bootstrap, CSRF, peekBody, backup symlink, quarantine/audit/OIDC/panic/scanner/CA/limitFor/quota/SVG/docs hardening (19 items) | ✅ done |
| M12 | Whole-codebase review round 3 — XFF ipaddress, CSRF bearer, OIDC nonce/TTL, audit thumbnail hash, backup device, cleanup, wizard, CA double-lock, quota backoff, scanner host-network, defusedxml, updater retry, rate-limiter sweep (41 items) | ✅ done — podman compose validated; 129 API tests + proxy Go tests pass |
| M13 | Whole-codebase review round 4 — SSRF denylist, text/plain bypass, body.decision bypass, compose wizard drift, OOM peekBody, DHCP attribution, text caps, video fail-closed, quota day, JWT rotation, OIDC SSRF, HMAC race, backup sqlite-copy (39 items) | ✅ done — 128 API tests + proxy/video-sampler Go tests pass; podman --profile core config validated |
| M14 | Whole-codebase review round 5 — fail-closed by default (setting `proxy.fail_closed`), SSRF rebinding/CGNAT/0.0.0.0/DNS fail-closed/Proxy nil, text head+tail/video sniff, video no_frames fail-closed gate, backup finally, peekBody drain, inspectText parallel 10s, quota crypto jitter, cleanup async, JWT IntegrityError, OIDC host, quota_day shared (18 P1 + 8 P2) | ✅ done — API tests + proxy/video-sampler Go tests pass; compose --profile core/scanner config validated |

## Open sub-items (tracked)

- [x] Wire OPA (`policies/seenoevil.rego`) into `/v1/decide` — done via sidecar + `policy_opa.py` (M2.1).
- [ ] Uncomment + validate the `backup` / `litestream` compose profiles.
- [ ] `vpn-wg` (wg-easy) profile: decide and document; Tailscale profile ships enabled.
- [x] Whole-codebase review hardening — all P0/P1/P2 findings triaged and fixed (G1-G6). Remaining P3 are residual/advisory and tracked in audit backlog.
- [x] Whole-codebase review round 2 — 19 findings (6 P1, 9 P2, 4 P3) fixed: XFF, token, CSRF, peekBody, backup, quarantine RBAC, audit, cleanup, CA cache, OIDC, panic HMAC, scanner, docs.
- [x] Whole-codebase review round 3 — 41 findings (2 P0, 13 P1, 23 P2, 3 P3) fixed: ipaddress XFF, CSRF bearer, Secure cookies, OIDC nonce/missing state, backup device, audit hash, quota CA scanner, proxy peekBody/limitFor, updater retry, wizard leak, docs.
- [x] Whole-codebase review round 4 — 39 findings (5 P0, 13 P1, 18 P2, 3 P3) fixed: SSRF denylist, text/plain + CT spoof bypass, body.decision bypass, compose /data/config.yaml drift, OOM+peekBody+tunnel, DHCP IP attribution, text caps 16→64, video sampler, SQLite lock, backup sqlite backup API, JWT rotation, OIDC https/Secure, HMAC race, quota day, wizard tokens, quarantine expiry, cleanup batch, settings validation, quota backoff jitter, scanner CIDR, video zero-frames, users last-admin, safesearch TLD.
- [x] Whole-codebase review round 5 — 18 P1 + 8 P2 fixed: tunnel rebinding + 0.0.0.0/CGNAT/unspecified + DNS fail-closed + Proxy nil + isGoogle label + addrIsBlocked/unmap, extractPlain/walkJSON head+tail + video ftyp + effectiveIsText head+tail + shouldInspect always-text, video no_frames ALLOW + inspect gating, backup finally/drain, peekBody drain, inspectText 8-parallel 10s, quota crypto jitter, cleanup naive UTC + to_thread + accumulating retry, classifier 5-retry, rateLimiter unknown, JWT IntegrityError, quota_day shared, decide classifier:block audit trust, OIDC all-hosts, last_seen refresh.
