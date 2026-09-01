# see-no-evil — Architecture (M0)

This document describes the v3 architecture as of milestone **M0** (scaffolding).
Nothing here is implemented yet; this is the contract the rest of the work will be
built against.

## Components, at a glance

```
                  ┌────────────────────────────────────────────────┐
                  │                  L A N                         │
                  │                                                │
   kid laptop ────┼──▶ DNS  (Blocky)         ──▶ upstream DoH      │
                  │                              (1.1.1.3 default) │
                  │                                                │
   kid laptop ────┼──▶ MITM Proxy (Go)  ──gRPC──▶  classifiers     │
                  │         │                                      │
                  └─────────┼──────────────────────────────────────┘
                            │
                ┌───────────┼─────────────┬───────────────┐
                ▼           ▼             ▼               ▼
         image-class    text-class    video-sampler    policy/OPA
          (Py+ONNX)      (Py+ONNX)     (Go+ffmpeg)      (embedded
                                                         in API)
                            │
                            ▼
                  API + DB (SQLite default | Postgres opt.)
                            │
                            ▼
                   Admin UI (React)
                            │
                  served via Caddy
                            │
                            ▼
                       you (browser)
```

Optional add-ons (each is a Docker Compose profile or Helm value):

| Add-on | Purpose |
|---|---|
| `scanner`        | Periodic nmap + mDNS / SSDP discovery; surfaces unknown devices in the UI. |
| `vpn-tailscale`  | Sidecar to expose admin UI over Tailnet. |
| `vpn-wg`         | wg-easy for self-managed WireGuard. |
| `observability`  | Vector → VictoriaMetrics + Grafana dashboards. |
| `redis`          | Replaces the embedded cache for HA / multi-replica deployments. |
| `postgres`       | Replaces SQLite for org-scale deployments. |
| `gpu`            | CUDA / OpenVINO / CoreML base image for classifiers. |

## Two completely separate TLS surfaces

This is the single most important thing to understand. Confusing them will lead
you down the wrong path.

| Surface | What it does | Who terminates TLS | Who issues the cert |
|---|---|---|---|
| **Admin UI / API** (e.g. `https://seenoevil.lan`) | Lets *you* log in to manage policies. | Caddy in front of the React UI + FastAPI. | Caddy's internal CA, or Let's Encrypt, or BYO. |
| **Client traffic interception** | Inspects kids' web traffic. | The MITM proxy (Go data-plane). | The **see-no-evil CA** that the user installs on their devices. |

The MITM proxy *is* itself a TLS terminator (that's what MITM means). Caddy is
**only** for the admin UI hostname. The two CAs and certs never mix:

- A device on the LAN trusts the see-no-evil **MITM CA** (installed manually or
  via MDM). It does **not** need to trust the admin UI cert.
- Your laptop (the admin) trusts the **admin UI cert** (Caddy-internal,
  Let's Encrypt, or BYO). It does **not** need to trust the MITM CA unless
  that admin laptop is also a "filtered" device.

If you don't want Caddy at all (e.g. you already run Traefik or are exposing the
UI through Tailscale Funnel), set `ui.reverse_proxy: external` and Caddy is not
started. The admin API is then served on `:8000` plain HTTP, intended to be
fronted by your own proxy.

## Data flow: a single inspected request

1. Client makes a request to `https://example.com/page`.
2. DNS resolution goes to Blocky → upstream DoH. If the domain is in a
   blocklist, return `0.0.0.0`. Done.
3. If allowed, the client connects to the MITM proxy (configured via PAC,
   transparent redirect, or DHCP option 252).
4. Proxy looks up the SNI host. If it matches `proxy.bypass_domains`, it
   becomes a TCP tunnel — done.
5. Otherwise, proxy presents a leaf cert minted on-the-fly from the see-no-evil
   CA, and terminates TLS.
6. Proxy looks up the source IP / MAC → device → profile, and asks the policy
   service: "is this URL allowed for profile X?" — text classifier may run on
   the path / query.
7. If allowed, proxy forwards upstream, terminates the upstream TLS itself, and
   reads the response.
8. Response body is fanned out (subject to `proxy.max_inspect_body`):
   - `text/html` and friends → text classifier
   - `image/*` → image classifier
   - `video/*` → video sampler → image classifier on N frames
9. Policy service combines URL verdict + body verdicts + profile thresholds:
   `allow` / `block` / `strip` (remove offending sub-elements) / `warn`.
10. Decision is logged to the audit table. Notification fires if configured.

## Egress posture

There are exactly three classes of containers from a network-egress
perspective:

1. **Allowed any egress** — the `updater` container, DNS (to upstream resolvers),
   and `notifications` (ntfy / webhooks if configured).
2. **Allowed limited egress** — the `proxy` container needs to reach upstream
   sites it's MITMing (that's the point). It does **not** reach
   `*.kernel-konsulting.example` or any telemetry endpoint, ever.
3. **No egress** — `image-classifier`, `text-classifier`, `video-sampler`,
   `api`, `ui`, `policy`, `scanner`. These talk only to other pods on the
   internal network.

Enforced by:
- Docker Compose: an `internal: true` network for class 3.
- Kubernetes: `NetworkPolicy` with `egress: []` for class 3.
- Optional belt-and-suspenders: an in-container nftables rule loaded at start.

## Storage layout (`pod.data_dir`, default `/data`)

```
/data/
├── policy.db            SQLite (only if db.url is sqlite)
├── ca/
│   ├── seenoevil-ca.crt The MITM CA cert (export to devices)
│   └── seenoevil-ca.key Encrypted with `age`
├── ui/
│   ├── cert.pem         Admin UI cert (when tls.mode = byo)
│   └── key.pem
├── models/
│   ├── image/freepik-v1.0.0.onnx
│   └── text/toxic-bert-v1.0.0.onnx
├── lists/
│   ├── stevenblack.hosts
│   └── oisd.txt
├── oui.csv              IEEE OUI database for vendor lookup
├── audit/               Rotated audit logs (also in DB; this is the rotation buffer)
└── backups/             Daily DB snapshots
```

## Why these choices?

- **Go for the data-plane proxy** — predictable latency, easy goroutine fan-out
  for body inspection, mature `goproxy` / `mitmproxy`-style libraries.
- **Python for classifiers** — the model ecosystem (Hugging Face, ONNX Runtime,
  Transformers) is overwhelmingly Python.
- **gRPC between proxy and classifiers** — streaming, well-typed, easy to swap
  classifier implementations later.
- **OPA for policy** — declarative, testable (`opa test`), externally
  reviewable. Wired as an `openpolicyagent/opa:0.68.0-static` sidecar on the
  `internal` network (`--bundle /policies`, `http://opa:8181`); API selects
  engine via `policy.engine: python | opa | auto` (default `python`, `auto`
  falls back to Python).
- **SQLite default** — one file, no daemon, fast enough for ~50 devices.
  Postgres swap is one config line.
- **Embedded cache default** — there is no good reason a single-replica home
  install needs Redis. Org installs flip one config flag.
- **Caddy for admin UI** — auto-HTTPS with internal CA, sensible defaults,
  ~25 MB RAM. Can be turned off entirely.

## What this architecture does NOT do (yet)

- Per-application identity (we have device → profile only; not "Kid-A's iPad
  *while logged into the family Apple ID*").
- Inspection of QUIC / HTTP/3. The proxy disables QUIC at the DNS layer
  (`Alt-Svc` stripping) and forces clients back to TCP.
- Cert-pinned mobile apps. Documented in `threat-model.md`.
- IPv6-first networks. Supported but not extensively tested at M0.
