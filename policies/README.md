# see-no-evil — OPA policy bundle

This directory holds Rego policies that will replace the in-tree Python policy
engine in a future milestone. For M2 the bundle is **scaffolding only**:

* `seenoevil.rego` mirrors the decision order implemented in
  [`services/api/src/seenoevil_api/policy.py`](../services/api/src/seenoevil_api/policy.py)
  in declarative form so the two stay in sync as the rule set grows.
* `seenoevil_test.rego` covers the same cases as `tests/test_policy.py`.

The Python `decide()` function remains the production code path; OPA evaluation
is wired in behind a feature flag (`policy.engine: opa`) in a follow-up PR.

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

Input shape (set by the proxy / API):

```json
{
  "url": "https://example.com/path",
  "host": "example.com",
  "path_query": "/path",
  "youtube_channel": null,
  "classifier_scores": {"porn": 0.1, "sexy": 0.0},
  "now_dow": 2,
  "now_time_minutes": 720,
  "minutes_used_today": 0,
  "profile": {
    "image_thresholds": {"porn": 0.6},
    "schedule": {},
    "quota_minutes_per_day": 0,
    "allow_domains": [],
    "deny_domains": [],
    "deny_url_keywords": [],
    "allow_youtube_channels": [],
    "deny_youtube_channels": []
  }
}
```

Output:

```json
{ "decision": "allow", "reason": "default" }
```
