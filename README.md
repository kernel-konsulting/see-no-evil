# see-no-evil

> Self-hosted, multi-container content-filtering pod for home networks, schools, and noncommercial orgs.

`see-no-evil` is an opinionated, mostly-offline pod that combines DNS blocking, MITM
TLS inspection, URL/body text classification, image classification, and video frame
sampling to filter NSFW (and other configured) content on a network you control. It
ships as a set of containers you can run on Podman, Docker Compose, or Kubernetes —
on a Raspberry Pi 5, an old NUC, dedicated hardware, or a self-managed cloud VM.

**Status:** v0.1 release candidate. Every milestone (M0–M9) is implemented:
DNS + MITM proxy + image/text/video classifiers, control-plane API + admin
UI, install wizard, scanner, OIDC, backup/restore, Litestream, full
observability with Grafana dashboards and vmalert. The pod is ready for
home and small-org pilot deployments. See [PLAN.md](PLAN.md) for the per-
milestone status table.

---

## Goals

- **Block NSFW content** at multiple layers: DNS, URL, page body text, images, and
  sampled video frames.
- **Friendly to non-experts:** sane defaults, install wizard, one config file,
  printable cert-install instructions for phones / laptops / TVs.
- **Privacy-respecting by default:** the pod can pull updates (blocklists, model
  weights, OUI database) but **inspected user content never leaves the box**.
- **Bring-your-own scale:** SQLite + embedded cache out of the box; swap to
  Postgres + Redis for orgs / non-profits with more devices.
- **Pluggable transport in:** access the admin UI directly on the LAN, behind your
  existing reverse proxy, via Tailscale, or via a bundled WireGuard option.
- **Truly offline-capable** after first start: model weights, blocklists, and
  fingerprint DBs are cached to a local volume.

## Non-goals

- A commercial SaaS. The license (PolyForm Noncommercial 1.0.0) explicitly forbids
  selling this software or running it as a paid service without permission.
- Replacing endpoint controls. MITM inspection cannot beat cert-pinned mobile
  apps; those land in the device-bypass / DNS-only flow and are documented as
  such.
- A general-purpose forward proxy. The MITM data-plane is purpose-built for
  classification and policy, not anonymity or caching.

## Architecture (one-line summary)

> A DNS resolver and an explicit MITM proxy sit on the LAN. The proxy fans
> request/response bodies out to image, text, and video classifiers over gRPC;
> a small policy service (with embedded OPA) decides allow / block / strip /
> warn. An admin API + React UI is served behind Caddy on a friendly hostname
> with its own (separate) cert.

See [`docs/architecture.md`](docs/architecture.md) for the full picture, including
the **two completely separate TLS surfaces** (admin UI cert vs. MITM CA).

## Quick start

```bash
git clone https://github.com/kernel-konsulting/see-no-evil.git
cd see-no-evil

# Build all container images (or build individual services):
./deploy/pods/seenoevil.sh build

# Start the pod (creates volumes, pod, and runs all containers):
./deploy/pods/seenoevil.sh up

# Then visit https://seenoevil.lan
# Admin email: admin@example.local
# Password: changeme (or set via SNE_INITIAL_ADMIN_PASSWORD env var)
```

For podman / docker compose deployments, see the [`deploy/compose/`](deploy/compose/) README.

Useful commands:
```bash
./deploy/pods/seenoevil.sh logs <service>     # e.g., logs proxy
./deploy/pods/seenoevil.sh status
./deploy/pods/seenoevil.sh down                # stops the pod (volumes preserved)
./deploy/pods/seenoevil.sh nuke                # down + remove volumes
```

See [docs/backup.md](docs/backup.md), [docs/oidc.md](docs/oidc.md), and
[docs/vpn.md](docs/vpn.md) for the optional profiles.

Hardware sizing notes are in [`docs/hardware-sizing.md`](docs/hardware-sizing.md).
The threat model and what this tool can and **cannot** protect against is in
[`docs/threat-model.md`](docs/threat-model.md).

## Configuration

A single, heavily commented [`config.example.yaml`](config.example.yaml) is the
source of truth for every knob. Copy it to `config.yaml` and edit. Highlights:

- `db.url` — SQLite by default, set to a `postgres://` URL for org deployments.
- `cache.kind` — `embedded` by default, set to `redis` for org deployments.
- `dns.upstreams` — defaults to Cloudflare `1.1.1.3` (family / NSFW-blocking).
- `proxy.ca` — auto-generate a CA on first start, or BYO if you run an internal PKI.
- `profiles` — first-class objects (Kid-A, Adults, Guests, …) that devices attach
  to. No per-kid logins required.
- `scanner.enabled` — optional nmap-based device discovery.

## Repository layout

```
.
├── LICENSE                     PolyForm Noncommercial 1.0.0
├── README.md                   you are here
├── config.example.yaml         the one config file users edit
├── docs/                       architecture, threat model, hardware sizing
├── deploy/compose/             docker-compose profiles (core, gpu, scanner, …)
├── services/                   one directory per container
│   ├── api/                    FastAPI control plane + DB
│   ├── ui/                     React admin UI
│   ├── proxy/                  Go MITM data-plane
│   ├── dns/                    Blocky wrapper + blocklist updater
│   ├── image-classifier/       Python + ONNX
│   ├── text-classifier/        Python + ONNX
│   ├── video-sampler/          Go + ffmpeg, calls image-classifier
│   ├── scanner/                Optional nmap + mDNS / SSDP discovery
│   └── updater/                Pulls blocklists, model weights, OUI DB
└── .github/workflows/          CI: lint, opa test, multi-arch buildx skeleton
```

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Free for personal, hobby, educational,
non-profit, and government use. **Selling this software, or running it as a paid
service, requires explicit permission.** See the license for the full terms.

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). Security issues: see [`SECURITY.md`](SECURITY.md).
