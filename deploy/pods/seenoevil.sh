#!/usr/bin/env bash
# =============================================================================
# see-no-evil — single-pod Podman orchestration
#
# Replaces docker-compose with a plain shell script that runs the whole stack
# as ONE Podman pod.  All containers share a network namespace, so services
# talk to each other over localhost and only the pod publishes host ports.
#
# Trade-off vs. the multi-pod / compose layout: there is no per-service network
# isolation — classifier containers can reach the loopback interface that the
# updater uses for egress.  The threat model in this layout relies on container
# user/uid separation and the fact that the classifier processes have no
# outbound DNS configured beyond what the pod's resolv.conf provides.
#
# Intra-pod port map (each container must listen on a unique port):
#   api               :8000
#   image-classifier  :50051   (metrics :9101)
#   text-classifier   :50052   (metrics :9102)
#   video-sampler     :50053   (metrics :9103)
#   ui (nginx)        :8081
#   proxy             :8080 / :8443  (metrics :9100)
#   dns (blocky)      :53     (metrics :4000/:4001)
#   caddy             :80 / :443
#
# Published on the host:
#   ${SNE_HTTP_PORT:-8088}  -> 80    (caddy admin UI, plain HTTP)
#   ${SNE_HTTPS_PORT:-8448} -> 443   (caddy admin UI, TLS)
#   ${SNE_PROXY_PORT:-8080} -> 8080  (mitm proxy, http CONNECT)
#   ${SNE_PROXY_TLS_PORT:-8443} -> 8443
#   ${SNE_DNS_PORT:-53}     -> 53/udp + 53/tcp   (override for rootless ports)
#
# Usage
#   ./seenoevil.sh build         # build all images
#   ./seenoevil.sh up            # create pod + volumes, start every container
#   ./seenoevil.sh down          # stop and remove the pod (volumes preserved)
#   ./seenoevil.sh nuke          # down + remove volumes
#   ./seenoevil.sh status        # podman pod ps + container health
#   ./seenoevil.sh logs <svc>    # tail logs (e.g. `logs proxy`)
#   ./seenoevil.sh restart <svc>
#   ./seenoevil.sh test-device   # print client setup instructions + export CA
#
# Environment overrides:
#   SNE_HTTP_PORT, SNE_HTTPS_PORT, SNE_PROXY_PORT, SNE_PROXY_TLS_PORT,
#   SNE_DNS_PORT, SNE_HOSTNAME, SNE_TLS_MODE, SNE_TLS_EMAIL,
#   SNE_IMAGE_TAG, SNE_UPDATE_INTERVAL
# =============================================================================
set -euo pipefail

# ── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
CONFIG_FILE="${REPO_ROOT}/config.example.yaml"

# ── Config ───────────────────────────────────────────────────────────────────
TAG="${SNE_IMAGE_TAG:-dev}"
REGISTRY_PREFIX="ghcr.io/kernel-konsulting/seenoevil"

POD_NAME="seenoevil"

VOL_DATA="seenoevil-data"
VOL_PROXY="seenoevil-proxy-data"
VOL_DNS="seenoevil-dns-data"
VOL_CADDY_DATA="seenoevil-caddy-data"
VOL_CADDY_CONFIG="seenoevil-caddy-config"
VOL_TAILSCALE="seenoevil-tailscale-state"

HTTP_PORT="${SNE_HTTP_PORT:-8088}"
HTTPS_PORT="${SNE_HTTPS_PORT:-8448}"
PROXY_PORT="${SNE_PROXY_PORT:-8080}"
PROXY_TLS_PORT="${SNE_PROXY_TLS_PORT:-8443}"
DNS_PORT="${SNE_DNS_PORT:-53}"
HOSTNAME_FQDN="${SNE_HOSTNAME:-seenoevil.lan}"
TLS_MODE="${SNE_TLS_MODE:-internal}"
TLS_EMAIL="${SNE_TLS_EMAIL:-}"
UPDATE_INTERVAL="${SNE_UPDATE_INTERVAL:-86400}"
INITIAL_ADMIN_PASSWORD="${SNE_INITIAL_ADMIN_PASSWORD:-changeme}"

# Tailscale (optional remote-access sidecar). Enable by either:
#   SNE_TAILSCALE=1 + SNE_TAILSCALE_AUTHKEY=tskey-...
#   SNE_TAILSCALE=1 + SNE_TAILSCALE_AUTHKEY_FILE=/path/to/keyfile
TAILSCALE_ENABLED="${SNE_TAILSCALE:-0}"
TAILSCALE_AUTHKEY="${SNE_TAILSCALE_AUTHKEY:-}"
TAILSCALE_AUTHKEY_FILE="${SNE_TAILSCALE_AUTHKEY_FILE:-${REPO_ROOT}/deploy/compose/secrets/tailscale_authkey.txt}"
TAILSCALE_HOSTNAME="${SNE_TAILSCALE_HOSTNAME:-seenoevil}"
TAILSCALE_IMAGE="${SNE_TAILSCALE_IMAGE:-ghcr.io/tailscale/tailscale:stable}"

# Build / image order — dependencies first.
ALL_SERVICES=(
    api
    image-classifier
    text-classifier
    video-sampler
    ui
    proxy
    dns
    updater
    caddy
    scanner
)

img() { echo "${REGISTRY_PREFIX}-$1:${TAG}"; }

log() { printf '\033[1;36m[seenoevil]\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31m[seenoevil]\033[0m %s\n' "$*" >&2; exit 1; }

cname() { echo "seenoevil-$1"; }

# ── Volumes / pod ────────────────────────────────────────────────────────────
ensure_volumes() {
    for v in "${VOL_DATA}" "${VOL_PROXY}" "${VOL_DNS}" "${VOL_CADDY_DATA}" "${VOL_CADDY_CONFIG}" "${VOL_TAILSCALE}"; do
        podman volume exists "$v" || podman volume create "$v" >/dev/null
    done
}

ensure_pod() {
    podman pod exists "${POD_NAME}" && return 0
    log "creating pod ${POD_NAME}"
    podman pod create \
        --name "${POD_NAME}" \
        --hostname "${HOSTNAME_FQDN}" \
        --publish "${HTTP_PORT}:80" \
        --publish "${HTTPS_PORT}:443" \
        --publish "${PROXY_PORT}:8080" \
        --publish "${PROXY_TLS_PORT}:8443" \
        --publish "${DNS_PORT}:53/udp" \
        --publish "${DNS_PORT}:53/tcp" \
        >/dev/null
}

# run_ctr <short-name> <image> [extra podman run flags...]
run_ctr() {
    local short="$1"; shift
    local image="$1"; shift
    local name; name="$(cname "${short}")"

    if podman container exists "${name}"; then
        podman start "${name}" >/dev/null
        return 0
    fi

    podman run -d \
        --pod "${POD_NAME}" \
        --name "${name}" \
        --restart unless-stopped \
        "$@" \
        "${image}" >/dev/null
}

# ── Build ────────────────────────────────────────────────────────────────────
cmd_build() {
    local services=( "$@" )
    [[ ${#services[@]} -eq 0 ]] && services=( "${ALL_SERVICES[@]}" )

    cd "${REPO_ROOT}"
    for svc in "${services[@]}"; do
        log "building ${svc}"
        podman build \
            -f "services/${svc}/Dockerfile" \
            -t "$(img "${svc}")" \
            .
    done
}

# ── Up ───────────────────────────────────────────────────────────────────────
cmd_up() {
    ensure_volumes
    ensure_pod

    # api ──────────────────────────────────────────────────────────────────
    # The proxy CA volume is mounted read-only so the api can serve the
    # MITM root certificate to clients via /v1/ca/cert.
    run_ctr api "$(img api)" \
        -v "${VOL_DATA}:/data" \
        -v "${VOL_PROXY}:/proxy-data:ro" \
        -v "${CONFIG_FILE}:/etc/seenoevil/config.yaml:ro" \
        -e "SEENOEVIL_CONFIG=/etc/seenoevil/config.yaml" \
        -e "SEENOEVIL_INITIAL_ADMIN_PASSWORD=${INITIAL_ADMIN_PASSWORD}" \
        -e "SEENOEVIL_MITM_CA_PATH=/proxy-data/ca/ca.crt"

    # image-classifier ─────────────────────────────────────────────────────
    run_ctr image-classifier "$(img image-classifier)" \
        -v "${VOL_DATA}:/data"

    # text-classifier ──────────────────────────────────────────────────────
    run_ctr text-classifier "$(img text-classifier)" \
        -v "${VOL_DATA}:/data"

    # video-sampler ────────────────────────────────────────────────────────
    run_ctr video-sampler "$(img video-sampler)" \
        -e "IMAGE_CLASSIFIER_ADDR=127.0.0.1:50051"

    # ui (nginx on :8081) ──────────────────────────────────────────────────
    run_ctr ui "$(img ui)"

    # proxy — talks to classifiers + api over loopback ─────────────────────
    run_ctr proxy "$(img proxy)" \
        -v "${VOL_PROXY}:/data" \
        -v "${CONFIG_FILE}:/etc/seenoevil/config.yaml:ro" \
        -e "CONFIG_PATH=/etc/seenoevil/config.yaml" \
        -e "IMAGE_CLASSIFIER_ADDR=127.0.0.1:50051" \
        -e "TEXT_CLASSIFIER_ADDR=127.0.0.1:50052" \
        -e "VIDEO_SAMPLER_ADDR=127.0.0.1:50053" \
        -e "API_ADDR=127.0.0.1:8000"

    # dns (blocky) ─────────────────────────────────────────────────────────
    run_ctr dns "$(img dns)" \
        -v "${VOL_DNS}:/data"

    # updater (egress) ─────────────────────────────────────────────────────
    run_ctr updater "$(img updater)" \
        -v "${VOL_DATA}:/data" \
        -e "SNE_UPDATE_INTERVAL=${UPDATE_INTERVAL}"

    # scanner — on-demand LAN sweep via POST :9104/scan ────────────────────
    # Periodic scans only run when scanner.enabled: true in config.yaml.
    # The control-plane HTTP endpoint is always up so the UI can trigger
    # ad-hoc sweeps. Needs CAP_NET_RAW for nmap ARP probes.
    run_ctr scanner "$(img scanner)" \
        -v "${CONFIG_FILE}:/etc/seenoevil/config.yaml:ro" \
        -e "SEENOEVIL_CONFIG=/etc/seenoevil/config.yaml" \
        -e "API_BASE=http://127.0.0.1:8000" \
        --cap-add=NET_RAW --cap-add=NET_ADMIN

    # caddy — reverse-proxies api (:8000) and ui (:8081) over loopback ─────
    run_ctr caddy "$(img caddy)" \
        -v "${VOL_CADDY_DATA}:/data" \
        -v "${VOL_CADDY_CONFIG}:/config" \
        -e "SNE_HOSTNAME=${HOSTNAME_FQDN}" \
        -e "SNE_TLS_MODE=${TLS_MODE}" \
        -e "SNE_TLS_EMAIL=${TLS_EMAIL}" \
        -e "SNE_API_UPSTREAM=127.0.0.1:8000" \
        -e "SNE_UI_UPSTREAM=127.0.0.1:8081"

    # tailscale (optional) ────────────────────────────────────────────────
    if [[ "${TAILSCALE_ENABLED}" == "1" ]]; then
        local key="${TAILSCALE_AUTHKEY}"
        if [[ -z "${key}" && -f "${TAILSCALE_AUTHKEY_FILE}" ]]; then
            key="$(tr -d '[:space:]' < "${TAILSCALE_AUTHKEY_FILE}")"
        fi
        if [[ -z "${key}" ]]; then
            log "tailscale enabled but no authkey — set SNE_TAILSCALE_AUTHKEY or populate ${TAILSCALE_AUTHKEY_FILE}"
        else
            log "starting tailscale sidecar (hostname=${TAILSCALE_HOSTNAME}, userspace mode)"
            run_ctr tailscale "${TAILSCALE_IMAGE}" \
                -v "${VOL_TAILSCALE}:/var/lib/tailscale" \
                -e "TS_AUTHKEY=${key}" \
                -e "TS_HOSTNAME=${TAILSCALE_HOSTNAME}" \
                -e "TS_STATE_DIR=/var/lib/tailscale" \
                -e "TS_USERSPACE=true" \
                -e "TS_EXTRA_ARGS=--accept-dns=false"
        fi
    fi

    log "pod up — try 'seenoevil.sh status' or open https://localhost:${HTTPS_PORT}"
}

# ── Down / nuke ──────────────────────────────────────────────────────────────
cmd_down() {
    if podman pod exists "${POD_NAME}"; then
        log "stopping pod ${POD_NAME}"
        podman pod stop "${POD_NAME}" >/dev/null || true
        podman pod rm   "${POD_NAME}" >/dev/null || true
    fi
}

cmd_nuke() {
    cmd_down
    for v in "${VOL_DATA}" "${VOL_PROXY}" "${VOL_DNS}" "${VOL_CADDY_DATA}" "${VOL_CADDY_CONFIG}" "${VOL_TAILSCALE}"; do
        podman volume exists "$v" && podman volume rm -f "$v" >/dev/null || true
    done
}

# ── Status / logs / restart ──────────────────────────────────────────────────
cmd_status() {
    podman pod ps --filter name="^${POD_NAME}$" \
        --format "table {{.Name}}\t{{.Status}}\t{{.NumberOfContainers}}"
    echo
    podman ps -a --filter pod="${POD_NAME}" \
        --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
}

cmd_logs() {
    [[ $# -ge 1 ]] || die "usage: $0 logs <service>"
    podman logs -f "$(cname "$1")"
}

cmd_restart() {
    [[ $# -ge 1 ]] || die "usage: $0 restart <service>"
    podman restart "$(cname "$1")"
}

# ── test-device ──────────────────────────────────────────────────────────────────────────────────────────
# Print step-by-step instructions for pointing a phone / laptop at the
# running pod so its traffic actually gets filtered.  Also dumps the MITM
# CA cert that has to be installed on the device for HTTPS interception.
cmd_test_device() {
    local host_ip=""
    # Try common interface names; first one with an IP wins.
    for iface in en0 en1 eth0 eth1 wlan0; do
        if command -v ipconfig >/dev/null 2>&1; then
            host_ip="$(ipconfig getifaddr "${iface}" 2>/dev/null || true)"
        elif command -v ip >/dev/null 2>&1; then
            host_ip="$(ip -4 addr show "${iface}" 2>/dev/null | awk '/inet /{print $2}' | cut -d/ -f1 | head -n1)"
        fi
        [[ -n "${host_ip}" ]] && break
    done
    [[ -n "${host_ip}" ]] || host_ip="<host-ip>"

    local ca_path="${SCRIPT_DIR}/seenoevil-ca.crt"
    if podman container exists seenoevil-proxy; then
        # Use `podman cp` (works on distroless containers — the proxy image
        # has no shell or `cat` to invoke via `podman exec`).
        if podman cp seenoevil-proxy:/data/ca/ca.crt "${ca_path}" 2>/dev/null; then
            log "exported MITM CA to ${ca_path}"
        else
            log "could not export MITM CA — fetch it later from the admin UI"
            ca_path="<not exported — use 'Setup' page in the admin UI>"
        fi
    fi

    cat <<EOF

Device setup — point a phone or laptop at this pod
--------------------------------------------------
  Host IP        : ${host_ip}
  DNS port       : ${DNS_PORT}    (Blocky)
  HTTP proxy port: ${PROXY_PORT}     (MITM)

1. DNS-only test (easiest — blocks domains, does not classify content):
     - Set the device's DNS server to ${host_ip} on port ${DNS_PORT}.
       Default is 53 (the standard); rootless Podman on macOS/Linux may
       need rootful Podman or CAP_NET_BIND_SERVICE to bind it. To use a
       higher port instead: SNE_DNS_PORT=1053 ./seenoevil.sh up
     - Visit a domain on the blocklist and confirm it fails to resolve.

2. HTTP/HTTPS proxy test (full content classification):
     - On the device, set HTTP and HTTPS proxy to ${host_ip}:${PROXY_PORT}.
     - Install and TRUST the MITM CA on the device:
         macOS / iOS: open ${ca_path}, install via Keychain / Profile,
                      then enable full trust under General > About > Cert Trust.
         Android    : Settings > Security > Encryption & credentials >
                      Install a certificate > CA certificate.
         Linux      : sudo cp ${ca_path} /usr/local/share/ca-certificates/
                      && sudo update-ca-certificates
     - Browse to any image-heavy site and watch the API audit log:
         podman logs -f seenoevil-api | grep decide

3. Watch decisions live:
     podman logs -f seenoevil-proxy
     curl -sk https://localhost:${HTTPS_PORT}/v1/audit/recent | jq .
EOF
}

# ── Dispatch ─────────────────────────────────────────────────────────────────
cmd="${1:-}"
[[ $# -ge 1 ]] && shift
case "${cmd}" in
    build)   cmd_build   "$@" ;;
    up)      cmd_up      "$@" ;;
    down)    cmd_down    "$@" ;;
    nuke)    cmd_nuke    "$@" ;;
    status)  cmd_status  "$@" ;;
    logs)    cmd_logs    "$@" ;;
    restart) cmd_restart "$@" ;;
    test-device) cmd_test_device "$@" ;;
    ""|help|-h|--help)
        sed -n '1,/^# ===/!{ /^# ===/q; s/^# \{0,1\}//; p; }' "$0" | sed -n '1,80p'
        ;;
    *)  die "unknown command: ${cmd} (try: build|up|down|nuke|status|logs|restart)" ;;
esac
