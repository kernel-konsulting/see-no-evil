# DNS (Blocky)

Thin wrapper around [Blocky](https://github.com/0xERR0R/blocky) configured
from `config.yaml` (`dns.*`). Pulls blocklists per `updates.lists`.

**M0:** Dockerfile stub only. M1 will pull the upstream Blocky image and
generate `blocky.yml` from our config schema.

## Responsibilities

- Resolve LAN client DNS queries.
- Apply blocklists configured under `dns.blocklists`.
- Forward to `dns.upstreams` (default: Cloudflare 1.1.1.3 family).
- Expose Prometheus metrics on `:4000`.
