# see-no-evil — Podman pod deployment

This is the **native Podman** deployment for see-no-evil. It runs the entire
stack as a **single Podman pod** (`seenoevil`) so containers share one network
namespace and talk to each other over `127.0.0.1`.

> **Why one pod?**
> Pods are Podman's first-class abstraction for grouping co-located containers.
> A single pod gives you one published port surface, one DNS namespace, and
> trivial intra-stack communication (everything is `localhost`). It also
> sidesteps every cross-pod pain point under rootless Podman:
> service-name DNS, `--network-alias`, `internal: true` quirks, and the
> healthcheck dependency-chain.
>
> The trade-off is that **per-service network isolation is gone** — the
> classifier containers share loopback with the updater, so they can in
> principle reach an outbound TCP port the updater opens. The remaining
> defences are uid separation, the classifiers binding only on `0.0.0.0` for
> their own gRPC port, and the absence of any outbound DNS resolver they're
> configured to use.

The compose file under [`deploy/compose/`](../compose/) is still present and
preserves the old multi-network isolation if you need it.

## Topology

One pod, nine containers, all on shared loopback:

| Container                  | Listens on (intra-pod)        | Notes                             |
|----------------------------|--------------------------------|-----------------------------------|
| `seenoevil-api`            | `:8000`                        | FastAPI control plane             |
| `seenoevil-image-classifier` | `:50051` (metrics `:9101`)   | gRPC + ONNX                       |
| `seenoevil-text-classifier`  | `:50052` (metrics `:9102`)   | gRPC + ONNX                       |
| `seenoevil-video-sampler`    | `:50053` (metrics `:9103`)   | ffmpeg sampler                    |
| `seenoevil-ui`             | `:8081`                        | nginx-served Vite SPA             |
| `seenoevil-proxy`          | `:8080`, `:8443` (`:9100`)     | MITM proxy                        |
| `seenoevil-dns`            | `:53` (`:4000/:4001`)          | Blocky DNS                        |
| `seenoevil-updater`        | —                              | One-shot loop, model/list fetch   |
| `seenoevil-caddy`          | `:80`, `:443`                  | Reverse proxy → ui + api          |

Published on the host (rootless-friendly defaults):

| Host port | → | Pod port  | What it serves                 |
|----------:|:-:|-----------|--------------------------------|
| `8088`    | → | `80`      | Caddy admin UI (HTTP)          |
| `8448`    | → | `443`     | Caddy admin UI (TLS)           |
| `8080`    | → | `8080`    | MITM proxy (CONNECT)           |
| `8443`    | → | `8443`    | MITM proxy (TLS-bridge)        |
| `1053`    | → | `53`/udp+tcp | Blocky DNS                  |

## Quick start

```sh
cd deploy/pods

# 1. build all images (one-time, slow)
./seenoevil.sh build

# 2. bring the stack up
./seenoevil.sh up

# 3. inspect
./seenoevil.sh status

# 4. open the admin UI
open https://localhost:8448
```

## Common operations

```sh
./seenoevil.sh logs api          # tail logs of one service
./seenoevil.sh restart proxy     # restart a single container
./seenoevil.sh down              # stop + remove the pod (volumes preserved)
./seenoevil.sh nuke              # down + delete volumes
```

## Environment overrides

All options have rootless-friendly defaults.

| Variable             | Default          | Purpose                                |
|----------------------|------------------|----------------------------------------|
| `SNE_HTTP_PORT`      | `8088`           | Caddy plain HTTP (host port → 80)      |
| `SNE_HTTPS_PORT`     | `8448`           | Caddy TLS (host port → 443)            |
| `SNE_PROXY_PORT`     | `8080`           | MITM proxy HTTP                        |
| `SNE_PROXY_TLS_PORT` | `8443`           | MITM proxy CONNECT                     |
| `SNE_DNS_PORT`       | `1053`           | Blocky DNS (host port → 53)            |
| `SNE_HOSTNAME`       | `seenoevil.lan`  | Admin UI hostname                      |
| `SNE_TLS_MODE`       | `internal`       | `internal` \| `acme` \| `off`          |
| `SNE_TLS_EMAIL`      | *(empty)*        | Required when `SNE_TLS_MODE=acme`      |
| `SNE_IMAGE_TAG`      | `dev`            | Image tag suffix                       |
| `SNE_UPDATE_INTERVAL`| `86400` (24 h)   | Updater sleep between cycles, seconds  |

To bind the privileged ports 80 / 443 / 53 directly (root or
`CAP_NET_BIND_SERVICE`):

```sh
SNE_HTTP_PORT=80 SNE_HTTPS_PORT=443 SNE_DNS_PORT=53 \
    ./seenoevil.sh up
```

## Migrating from compose

If you previously ran the compose stack:

```sh
cd deploy/compose
podman compose --profile core down -v   # stop and clear named volumes
cd ../pods
./seenoevil.sh up
```

The pod stack uses different volume names (`seenoevil-data` vs
`seenoevil_data`) so the two deployments don't conflict, but they share the
same image tags — `./seenoevil.sh build` will rebuild them in place.

## Production: Quadlet

On a Linux host with systemd, Quadlet (`.pod` / `.container` unit files in
`/etc/containers/systemd/`) is the recommended path for auto-start, log
integration, and graceful upgrades. The single-pod layout in this script
maps cleanly onto a single Quadlet `.pod` file plus one `.container` file
per service. A ready-made Quadlet bundle ships in
[`deploy/quadlet/`](../quadlet/) once M11 lands; until then the script is
the supported path.
