---
artifact_contract: ce-unified-plan/v1
artifact_readiness: implementation-ready
product_contract_source: ce-plan-bootstrap
plan_output: md
created: 2026-08-31
audience: implementer + reviewer
---

# Plan — OPA Sidecar Wiring (M2.1)

## Goal Capsule

Wire `policies/seenoevil.rego` (today scaffolding, `opa test` only) into the production `POST /v1/decide` path via an `openpolicyagent/opa` sidecar on the `internal` network, feature-flagged `policy.engine: python | opa | auto` (default `python`, `auto` falls back to Python on OPA failure), with Python as canonical spec — fixing Rego drift (enforce_allowlist, neutral/drawing, global rules, keyword decoding) before promotion.

## Product Contract

### Requirements

* **R1 — Single policy engine with flag:** `config.yaml: policy.engine` controls the decide path; `auto` means OPA-primary with Python fallback, never hard-fail on OPA 503/timeout.
* **R2 — Sidecar deployment:** OPA runs as `opa:8181` on `internal:true`, ro-mounted `policies/` bundle, healthcheck, no egress; `api` reaches it via `http://opa:8181`.
* **R3 — Python-canonical parity:** Rego is fixed to match `services/api/src/seenoevil_api/policy.py:264` semantics (allowlist gating, _NON_BLOCKING, global_rules, URL-decode, threshold fallback, reason suffixes).
* **R4 — Preserve fail-closed/pre-block:** panic-relax and proxy `classifier:*` forced-block short-circuits (`services/api/src/seenoevil_api/routers/decide.py:271,273`) stay outside OPA; OPA handles only pure policy (schedule/quota/deny/keyword/youtube/allowlist/classifier).
* **R5 — Drift guard:** A parity test feeds the same matrix to Python and OPA and fails on decision/reason divergence; `opa fmt`/`opa test` remain green.
* **R6 — Observability:** Decide path emits metric/log for `policy_engine=python|opa` and `opa_fallback` counter; `/healthz` does not depend on OPA.
* **R7 — No breaking compose:** `docker compose --profile core config` and `--profile core --profile opa` both validate; docs and `config.example.yaml` updated.

### Acceptance Examples

* **AE1:** `policy.engine=python` → `POST /v1/decide` with `deny_domains=["tiktok.com"]` blocks via Python; OPA down still 200.
* **AE2:** `policy.engine=opa` → same input blocks via OPA; `opa` stopped → 503 if `auto` is not set, 200 via fallback if `auto`.
* **AE3:** `allow_domains=["example.com"]` without `enforce_allowlist` → `allow` for `other.com` on both engines; with `enforce_allowlist=true` → `block not_in_allowlist`.
* **AE4:** `classifier_scores={"image:porn":0.7,"image:neutral":0.99}` → block on `porn`, ignore `neutral` on both engines.
* **AE5:** Drift test with 30 cases (schedule window wrap, quota, deny wins over allow, keyword encoded, youtube channel @-strip, global deny) passes vs both.

### Actors

* **A1 — Proxy** (Go mitm) — calls `POST /v1/decide` with `Authorization: Bearer SEENOEVIL_PROXY_TOKEN`, supplies `url`, `content_type`, `classifier_scores`, `client_ip`.
* **A2 — Admin** — edits `config.yaml` / `policy.engine` or runtime settings via `PUT /v1/settings`.

### Key Flows

* **F1 — Decide via OPA (sidecar):** `proxy -> api /v1/decide` → `decide.py:228` resolves device/profile/global_rules → short-circuits panic/forced-block → `policy_opa.decide()` POSTs normalized input to `http://opa:8181/v1/data/seenoevil/policy/decision` → maps response to `DecisionOutput` → audit/quarantine/commit.
* **F2 — Fallback:** OPA timeout/5xx/parse error → log + `opa_fallback_total` + re-evaluate via `policy.py:decide` when `engine=auto`; when `engine=opa` bubble 503 (strict).
* **F3 — Deploy:** `docker compose --profile core --profile opa up -d` starts `opa` alongside `api` on `internal`; `api` `depends_on: opa: condition: service_healthy` only when opa profile active (or soft dep).

---

## Implementation Units

### U1 — Config: `policy` block

* **Files:** `services/api/src/seenoevil_api/config.py:323` (add `PolicyConfig`), `config.example.yaml` (add `policy:`), `services/api/tests/test_config.py`
* **Covers:** R1, R7
* **Behavior:** Add `class PolicyConfig(_Base): engine: Literal["python","opa","auto"]="python"; opa_url: str="http://opa:8181"; opa_timeout_ms: int=1500` with validator: `engine in ("opa","auto") => opa_url required`. Plumb into `AppConfig.policy`. Defaults keep current behavior. Document env override `SEENOEVIL_POLICY_ENGINE` / `OPA_ADDR` mirroring `AGENTS.md:127` pattern.
* **Tests:**
  * `test_policy_config_defaults` — default `python`, `opa_url` default.
  * `test_policy_config_opa_requires_url` — `engine=opa` with empty url → ValidationError.
  * `test_example_config_validates` still passes after adding `policy:` to example.

### U2 — Rego parity fix (Python-canonical)

* **Files:** `policies/seenoevil.rego:1`, `policies/seenoevil_test.rego:1`, `policies/README.md:30` input contract
* **Covers:** R3, R5
* **Behavior:** Extend input to `input.global_rules{allow_domains,enforce_allowlist,deny_domains,deny_url_keywords,apply_domain_rules,apply_url_rules,image_thresholds}` and `input.config_thresholds` plus `input.panic_relax` (or keep panic outside). Implement:
  * `global_deny_domain_block`, `global_allow_domain_block`, `global_not_in_allowlist`, `global_deny_keyword_block` in priority order before profile denies (`policy.py:290-305`).
  * Gate `allowlist_block` on `enforce_allowlist` (`policy.py:337-349`), reason `not_in_allowlist:{host}` and `global_not_in_allowlist`.
  * Classifier: `non_blocking := {"neutral","drawing","drawings","safe"}`, `label := split(cls,":")[1]` + fallback `threshold := profile.image_thresholds[label] // global_rules.image_thresholds[label] // config_thresholds[label]`, skip when none, enrich reason `classifier:{label}`.
  * Keyword: use `lower(input.url)` (or add `input.url_text` pre-decoded via `unquote_plus` in adapter) not just `path_query`; match host+path+query.
  * Keep `schedule_block:44` but ensure `host_matches:271` and `channel_matches:303` stay aligned; add `drawing` alias normalization.
* **Tests (OPA):**
  * `opa test -v policies/` still passes.
  * Add `test_global_rules`, `test_enforce_allowlist_off_allows`, `test_neutral_not_blocking`, `test_threshold_fallback_runtime`, `test_keyword_url_decode` in `seenoevil_test.rego`.

### U3 — OPA adapter + engine switch in decide router

* **Files:** `services/api/src/seenoevil_api/policy_opa.py` (new), `services/api/src/seenoevil_api/routers/decide.py:18,286`, `services/api/pyproject.toml:14` (no new dep — `httpx==0.28.1` already), `services/api/src/seenoevil_api/policy.py:264` (untouched)
* **Covers:** R1, R4, R6
* **Behavior:** New module:
  ```python
  def build_opa_input(profile: ProfileView, inputs: DecisionInput, *, global_rules: GlobalRules, config: AppConfig) -> dict
  # normalizes: host via urlparse, path_query, youtube_channel via _extract_youtube_channel, now_time_minutes via time->minutes, classifier_scores flat map, profile.image_thresholds+schedule etc, global_rules+config_thresholds, _NON_BLOCKING set not sent (handled in Rego)
  def opa_decide(input: dict, *, url: str, timeout: float) -> DecisionOutput  # POST /v1/data/seenoevil/policy/decision, parse decision.reason
  ```
  Router change: in `post_decide` after `if panic_state.active:271` and `elif body.decision==block and reason.startswith("classifier:"):273` (keep outside OPA), branch:
  ```
  if config.policy.engine in ("opa","auto"):
    try: opa_out = opa_decide(..., timeout=config.policy.opa_timeout_ms/1000)
    except Exception as e:
      if config.policy.engine=="auto": log warning + metrics + fall through to Python decide(); else: raise 503
  else: python_out = decide(...)
  ```
  Preserve `_global_rules:203` and `now_parts:371` conversion (ZoneInfo already). Map OPA `{"decision":"allow","reason":"..."}` → `DecisionOutput`. On fallback, audit reason must indicate fallback (e.g. `log.info opa_fallback` + `policy_engine` label on prometheus `decide_requests_total`).
* **Tests:**
  * Unit: `test_build_opa_input_maps_host_and_channel` — url `https://www.tiktok.com/@handle?x=1` → `host=tiktok.com`, `youtube_channel` null etc.
  * Unit: mocked `httpx` POST returns `{"result":{"decision":"block","reason":"deny_domain:tiktok.com"}}` → `DecisionOutput` correctly.
  * Integration: `test_decide_engine_auto_fallback_on_opa_500` — mock `httpx.post` 500 → still 200 via Python.
  * No new asyncio; `decide.py:232` stays sync (uses `httpx.Client` 1500ms timeout — not async to avoid event-loop coupling).

### U4 — Compose: OPA sidecar

* **Files:** `deploy/compose/docker-compose.yml:33,86,184` (networks, api, proxy), `deploy/pods/seenoevil.sh:1`, `docs/architecture.md:22`, `services/api/Dockerfile:1` (no change)
* **Covers:** R2, R7
* **Behavior:** Add service `opa:`
  ```yaml
  opa:
    profiles: ["opa"]
    image: openpolicyagent/opa:0.68.0-static  # pin to lint.yml:125
    restart: unless-stopped
    networks: [internal]
    command: ["run","--server","--addr=0.0.0.0:8181","--bundle","/policies"]
    volumes: ["../../policies:/policies:ro"]
    healthcheck: {test: ["CMD","wget","-qO-","http://localhost:8181/health"], interval: 10s}
    expose: ["8181"]
  ```
  `api` adds `environment: OPA_ADDR: "http://opa:8181"` and stays on `internal` only (egress `internal:true:40` preserved). `api` `depends_on: opa: condition: service_healthy` is **soft** — only when `opa` profile active; otherwise `api` must boot without `opa` (so `core` alone still works). Document profile `opa` in header comments `4-15` and `README.md` compose examples.
* **Tests/Checks:**
  * `docker compose -f deploy/compose/docker-compose.yml --profile core config` passes.
  * `docker compose -f deploy/compose/docker-compose.yml --profile core --profile opa config` passes and `opa` is `internal` only (no `edge`).

### U5 — Observability + docs

* **Files:** `services/api/src/seenoevil_api/routers/decide.py:1` (prom counter), `services/api/README.md:136` (remove deferred line), `config.example.yaml:448` (policy block docs), `docs/architecture.md:22,147`
* **Covers:** R6, R7
* **Behavior:** Add `decide_requests_total{engine="python|opa|fallback"}` and `opa_fallback_total` counters (reuse `prometheus_client` pattern from `notifications.py`). Log at `INFO` on fallback with `exc_info`. Update `docs/architecture.md:147` “OPA for policy — sidecar, testable” and `services/api/README.md:136` to “OPA wired via sidecar, flag `policy.engine`”.

### U6 — Drift/parity harness

* **Files:** `services/api/tests/test_policy_opa_parity.py` (new), `.github/workflows/lint.yml:158` (api-tests job)
* **Covers:** R5
* **Behavior:** Param matrix of ~30 cases (same as `services/api/tests/test_policy.py:19` helpers): schedule allow/deny + wrap midnight, quota, global deny/allow, enforce_allowlist on/off, deny wins over allow, keyword plain+encoded, youtube channel @-strip + channel allowlist empty vs missing, classifier porn/neutral/drawing with fallback, default allow. For each case: `py_out = decide(...)` and `opa_out = opa_decide(build_opa_input(...))` via live `http://opa:8181` when reachable else `pytest.mark.skip` (or `opa eval --data policies/seenoevil.rego --input input.json "data.seenoevil.policy.decision"` fallback subprocess). Assert `py_out.decision==opa_out.decision` and `reason` normalized (strip `global_*:` prefix vs suffix but require same suffix `tiktok.com`/`classifier:porn`).
* **Tests:**
  * `test_opa_parity_matrix` (30 subcases).
  * `test_opa_parity_neutral_not_blocking` isolated.
  * CI `api-tests:158` installs `httpx` already; add `opa eval` stub or start OPA container in job (or skip if `OPA_ADDR` unreachable).

---

## Decision Table

| KTD | Options | Chosen | Rationale |
|---|---|---|---|
| **K1: Deployment** | embedded lib vs sidecar vs CLI subprocess | **Sidecar** | User chose; isolation, no api image bloat, mirrors `internal:true:40` pattern. Latency ~5ms vs 30ms CLI. |
| **K2: Canonical spec** | Python vs Rego vs merge | **Python canonical** | Python has `GlobalRules:86`, runtime thresholds `policy.py:352`, `_NON_BLOCKING:37`, URL-decode `204`, suffix reasons `313` — all prod-tested. Fixing Rego is smaller. |
| **K3: Flag** | config vs env vs hard switch | **Config flag + fallback** | User chose; `AppConfig:323` owns single `config.yaml`; `auto` prevents hard fail when OPA 503, matches `AGENTS.md:38` validation gate. |
| **K4: Short-circuit ownership** | OPA handles panic/forced-block vs host Python | **Host Python** | `decide.py:271,273` panic/forced-block are outside policy (fail-closed); OPA stays pure policy per `policy.py:264` doc order. Simpler Rego. |
| **K5: HTTP client** | `httpx.AsyncClient` vs `httpx.Client` | **Sync `httpx.Client`** | `decide.py:232` `post_decide` is sync (`def`, not `async`), and `cleanup.py:108` already uses `asyncio.to_thread` for sync IO. Sync client avoids event-loop coupling. |
| **K6: Timeout** | 1s vs 1.5s vs 5s | **1500ms** | `services/api/src/seenoevil_api/routers/scanner.py:28` uses 120s for nmap but policy must be fast; 1.5s aligns with `quota_day:56` heartbeat budget. |

---

## File Map

* `services/api/src/seenoevil_api/config.py` — add `PolicyConfig`
* `config.example.yaml` — add `policy:` block
* `policies/seenoevil.rego` — parity fix
* `policies/seenoevil_test.rego` — add global/enforce/neutral cases
* `services/api/src/seenoevil_api/policy_opa.py` — new adapter
* `services/api/src/seenoevil_api/routers/decide.py` — engine switch
* `deploy/compose/docker-compose.yml` — `opa` service
* `services/api/tests/test_config.py` — policy config tests
* `services/api/tests/test_policy_opa_parity.py` — parity harness
* `services/api/README.md`, `docs/architecture.md` — docs

---

## Risks & Mitigations

* **RISK: Sidecar not on `core` → `auto` fallback hot-loop logs.** Mitigate: `auto` logs at `WARNING` with sampling, `opa_fallback_total` metric, and `api` `depends_on` soft (no hard dep when `opa` profile off).
* **RISK: Input normalization drift (host, youtube_channel, now_time_minutes).** Mitigate: `build_opa_input` reuses `policy.py:163` `_host_matches`, `_extract_youtube_channel:224`, `_parse_window:122` helpers — share code, not re-derive.
* **RISK: Latency.** Mitigate: 1500ms timeout + `auto` fallback; benchmark 30-case matrix in CI logs `latency_ms` like `proxy` `classifierErrors:65`.

---

## Test Scenarios (executable)

* **T1 — Policy engine selection:** `policy.engine=python` uses Python; `engine=opa` with `opa` down returns 503; `engine=auto` with `opa` down still 200.
* **T2 — Global rules:** `global_deny_domains=["evil.com"]` blocks before profile allow; `global_deny_keywords=["nsfw"]` with `url="https://example.com/path?query=NsFw"` blocks.
* **T3 — Allowlist gating:** `allow_domains=["a.com"]` + `enforce_allowlist=false` allows `b.com`; `true` blocks `b.com` with `not_in_allowlist:b.com`.
* **T4 — Classifier filtering:** `classifier_scores={"neutral":0.99,"porn":0.4}` with `threshold porn:0.5` allows; `porn:0.6` blocks.
* **T5 — Keyword encoding:** `deny_url_keywords=["hello world"]` with `url="https://example.com/search?q=hello%20world"` blocks on both engines.
* **T6 — YouTube:** `host=www.youtube.com` + `channel=@LinusTechTips` vs pattern `linustechtips` matches regardless of `@`.
* **T7 — Fallback:** `opa` returns 500 → `auto` falls back, metric increments, response still 200 with Python reason.

---

## Sequencing

1. U1 (config) → unblocks U3.
2. U2 (Rego fix) in parallel — must land before U6 can pass.
3. U4 (compose) can land anytime, but U3 needs it for manual `curl` testing.
4. U3 (adapter) → U5 (metrics/docs) → U6 (parity harness).

---

## Out of Scope

* Removing Python engine (hard switch) — deferred; `auto` keeps both.
* OPA bundle signing / /v1/data auth — sidecar is `internal:true`, no token needed (API already bearer-guards `/v1/decide`).
* Text body inspection policy — stays in text-classifier, not OPA.

---

## Confidence Check

* **Repo patterns verified:** `AppConfig:323` `extra="allow"`, `httpx==0.28.1` already deps, `internal:true:40` egress, `opa:0.68.0-static` pinned.
* **Ambiguity remaining:** OPA healthcheck command (`wget` vs `curl`) depends on OPA image busybox — verify during U4 compose validation (`wget` matches `ui: healthcheck`).

**Plan ready at `docs/plans/2026-08-31-opa-sidecar-wiring-plan.md`. What would you like to do next?**

1. Create GitHub issues from this plan
2. Start implementation via `ce-work` (execute U1-U6)
3. Refine the plan (change KTD, scope, or sequencing)
4. Export/share the plan

