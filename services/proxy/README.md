# Proxy (MITM data-plane)

Go HTTP/HTTPS proxy that terminates TLS using the see-no-evil CA, fans request
and response bodies to the classifier services over gRPC, and applies the
policy decision returned by the API.

**M0:** Dockerfile stub only. No source yet.

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
