# Proxy (MITM data-plane)

Go HTTP/HTTPS proxy that terminates TLS using the see-no-evil CA, fans request
and response bodies to the classifier services over gRPC, and applies the
policy decision returned by the API.

**Status:** M1.3 ✅ data-plane / M2 ✅ SafeSearch + YouTube cookies / M4 ✅ text body extract & strip mode.

## Responsibilities

- Listen on 8080 (HTTP) and 8443 (HTTPS-CONNECT MITM).
- Mint per-host leaf certs on demand from the configured CA.
- Honor `proxy.bypass_domains` (SNI-only TCP tunnel).
- Strip `Alt-Svc` to disable client QUIC fallback.
- Inject SafeSearch cookies / query params per `proxy.safesearch`.
- Stream bodies up to `proxy.max_inspect_body` to classifiers; pass through
  larger ones.
- Look up source → device → profile via the API.
- Emit per-decision audit records.

## Text inspection (M4)

The proxy extracts natural-language text out of HTML and JSON response bodies
(via `internal/textextract`) before sending it to the text classifier — feeding
raw markup or token IDs would just generate noise. Skips `<script>`, `<style>`,
`<noscript>`, `<template>`, and `<svg>` subtrees in HTML; in JSON, only string
values that look like prose (≥16 runes, contain a space, not a URL) are
classified.

Behaviour is controlled by `proxy.text_inspection` in `config.yaml` or by env
vars on the proxy container:

| Mode    | Env: `TEXT_INSPECTION_MODE` | Behaviour |
|---------|-----------------------------|-----------|
| `off`   | `off`                       | Skip text classification entirely. |
| `block` | `block` (default)           | Block the whole page when any segment is flagged. |
| `strip` | `strip`                     | Re-render the body with flagged segments replaced by `proxy.text_inspection.redaction`. |

| Setting | Env var | Default |
|---|---|---|
| `proxy.text_inspection.nsfw_threshold` | `TEXT_NSFW_THRESHOLD` | `0.5` |
| `proxy.text_inspection.redaction`      | `TEXT_REDACTION`      | `[content removed by see-no-evil]` |
