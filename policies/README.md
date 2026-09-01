# see-no-evil — OPA policy bundle

This directory holds Rego policies for the OPA sidecar (M2.1).

* `seenoevil.rego` mirrors the decision order in
  [`services/api/src/seenoevil_api/policy.py`](../services/api/src/seenoevil_api/policy.py)
  in declarative form (Python is canonical).
* `seenoevil_test.rego` covers the same cases as `services/api/tests/test_policy.py`.

Selection is via `policy.engine` in `config.yaml` (`python` | `opa` | `auto`, default
`python`; `auto` is OPA-primary with Python fallback). The sidecar is
`openpolicyagent/opa:0.68.0-static` on `internal` (`--bundle /policies`,
`http://opa:8181`). See `services/api/src/seenoevil_api/policy_opa.py` for the
input contract and `deploy/compose/docker-compose.yml` `opa` profile.

## Running the tests

```bash
opa fmt --diff .
opa test -v .
```

## Bundle layout

| File | Purpose |
|---|---|
| `seenoevil.rego` | Main policy (decision rules). Package: `data.seenoevil.policy` |
| `seenoevil_test.rego` | Unit tests for the main policy |

## Decision contract

Input shape (built by `policy_opa.build_opa_input`):

```json
{
  "url": "https://example.com/path",
  "host": "example.com",
  "path_query": "/path",
  "url_text": "https://example.com/path example.com /path  (decoded, lower)",
  "youtube_channel": null,
  "classifier_scores": {"porn": 0.1, "sexy": 0.0, "image:porn": 0.1},
  "now_dow": 2,
  "now_time_minutes": 720,
  "minutes_used_today": 0,
  "panic_relax": false,
  "profile": {
    "image_thresholds": {"porn": 0.6},
    "schedule": {},
    "quota_minutes_per_day": 0,
    "allow_domains": [],
    "deny_domains": [],
    "deny_url_keywords": [],
    "allow_youtube_channels": [],
    "deny_youtube_channels": [],
    "enforce_allowlist": false
  },
  "global_rules": {
    "allow_domains": [],
    "deny_domains": [],
    "deny_url_keywords": [],
    "enforce_allowlist": false,
    "apply_domain_rules": true,
    "apply_url_rules": true,
    "image_thresholds": {}
  },
  "config_thresholds": {"porn": 0.6, "sexy": 0.85}
}
```

Output:

```json
{ "decision": "allow", "reason": "default" }
```
