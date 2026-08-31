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
- Re-fetch ranged `video/*` responses as full bodies before sampling so MP4
  range requests do not bypass frame inspection.
- Look up source → device → profile via the API.
- Emit per-decision audit records.
- Attach clear audit thumbnails for decoded image responses. JPEG, PNG, GIF,
  and WebP are decoded locally; unsupported image formats still classify but
  may show the UI placeholder.
- Mark inspected responses and local block pages as `Cache-Control: no-store`
  so long-lived browser caches do not keep serving previously allowed images
  after classifier behavior changes.
- For blocked image responses, return a no-store SVG replacement sized from
  the original image where possible and labelled `see no evil blocked`.
- Fail closed for video sampler infrastructure/decode failures; unscannable
  videos are blocked with a `classifier:video:*` reason instead of silently
  passing through.
- Log every blocked request with a non-empty `reason` field so operators can
  tell whether the block came from a classifier, policy rule, quota, schedule,
  or global list.

## Block logging

Every request that returns the local block page emits a structured INFO log
entry named `request blocked` with `url`, `method`, `host`, `content_type`,
`device_mac`, and `reason`. Classifier-triggered blocks use reasons such as
`classifier:image:porn`, `classifier:video:porn`, or `classifier:text:nsfw`;
policy/API blocks use reasons like `deny_domain`, `global_deny_keyword`,
`quota`, or `schedule`.

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

**M12 hardening (whole-codebase):**

- `LeafCache` now canonicalizes via `canonicalHost()` (strip port, lower-case) and uses double-checked locking to avoid mint under lock; `DNSNames` lower-cased.
- `peekBody` unified via `hasImageMagic()`/`capped()` helpers, logs `peekBody read error` and increments `proxy_peek_body_errors_total`.
- `limitFor` deduped via `capped()`, `shouldInspectWithBody`/`looksLikeImage` share `hasImageMagic()`.
- Quota `Reporter` now batch-locks in `accumulate()` and has per-IP exponential backoff with jitter in `flush()`.

**M13 hardening (round 4 — 39 items):**

- **SSRF denylist:** `Transport.DialContext` `secureDial` blocks `127/10/172.16/192.168/169.254/fd00/fe80` literal + hostname-resolved private IPs; `tunnel` pre-dial check + 2m deadline (F01/F11).
- **Text bypass:** `textextract.IsSupported` now `text/*`, `application/xml`, `+xml`, `javascript`, `css`; `Extract` + `Strip` handle `extractPlain` line-split; `effectiveIsText` `text/*` + printable-ratio fallback; `shouldInspectWithBody` sniffs `hasImageMagic` on any CT (F02/F07).
- **Capacity:** `inspectionSem` 50 concurrent caps 500MiB OOM; `limitFor` generic → `hardMaxImage` 20MiB sniff cap; `peekBody` returns `MultiReader` on error; `inspectText` cap 16→64 head+tail; `walkJSON` cap 32→64 (F05/F08/F18).
- **Fail-closed:** `proxy.fail_closed` defaults `true` when key absent via `yaml.Node` walk; `FailClosed` true blocks on classifier/policy/sampler zero-frames/`no_frames`; image `INVALID_ARGUMENT` → block (F09/F10/F16).
- **Video:** zero frames now `ACTION_BLOCK video_sampler:no_frames` for FailClosed; `isVideoSamplerFailure` + `FramesScored==0` check.
- **SafeSearch:** `isGoogle` now `strings.Contains(..., "google.")` covers `google.co.uk` etc (F36); YouTube `youtube-nocookie.com` already included.
