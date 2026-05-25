#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
POD_SCRIPT="${REPO_ROOT}/deploy/pods/seenoevil.sh"

TAG="${SNE_IMAGE_TAG:-dev}"
REGISTRY_PREFIX="${SNE_REGISTRY_PREFIX:-ghcr.io/kernel-konsulting/seenoevil}"
POD_NAME="${SNE_POD_NAME:-seenoevil}"

DATASET_DIR="${REPO_ROOT}/test_data"
OUTPUT_DIR="${SCRIPT_DIR}/results/local-podman"
BASELINE_PATH=""

USERNAME="${SEENOEVIL_EMAIL:-admin@example.local}"
PASSWORD="${SEENOEVIL_PASSWORD:-changeme}"
PROXY_URL="${SEENOEVIL_PROXY_URL:-http://127.0.0.1:8080}"
API_URL="${SEENOEVIL_API_URL:-http://127.0.0.1:8000}"

WRITE_BASELINE=0
FAIL_ON_CHANGE=0
FORCE_BUILD=0
VIEW_REPORT=0
VIEW_ONLY=0
SCORE_TOLERANCE=""
AUDIT_TIMEOUT=""
POLL_INTERVAL=""

usage() {
    cat <<'EOF'
Usage: ./tests/proxy-regression/run.sh [options]

Runs the proxy image-regression harness against the local Podman pod.

Options:
  --build                 Force a fresh pod image build before running.
  --write-baseline        Accept the current run as the new baseline.
  --baseline PATH         Compare against a specific baseline JSON under the repo.
  --fail-on-change        Exit non-zero when decisions or scores drift.
  --view                  Open the generated HTML report after the run.
  --view-only             Open the latest HTML report without re-running the harness.
  --dataset-dir PATH      Read images from this repo-local directory.
  --output-dir PATH       Write results under this repo-local directory.
  --username VALUE        API login email. Default: admin@example.local
  --password VALUE        API login password. Default: changeme
  --proxy-url URL         Proxy URL used inside the pod. Default: http://127.0.0.1:8080
  --api-url URL           API URL used inside the pod. Default: http://127.0.0.1:8000
  --score-tolerance NUM   Forwarded to the regression harness.
  --audit-timeout NUM     Forwarded to the regression harness.
  --poll-interval NUM     Forwarded to the regression harness.
  -h, --help              Show this help.

Examples:
  ./tests/proxy-regression/run.sh --write-baseline --view
  ./tests/proxy-regression/run.sh --fail-on-change
  ./tests/proxy-regression/run.sh --view-only
EOF
}

log() {
    printf '[proxy-regression] %s\n' "$*" >&2
}

die() {
    printf '[proxy-regression] %s\n' "$*" >&2
    exit 1
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

image_name() {
    printf '%s-%s:%s' "${REGISTRY_PREFIX}" "$1" "${TAG}"
}

ensure_repo_local_path() {
    local path="$1"

    case "${path}" in
        "${REPO_ROOT}"/*|"${REPO_ROOT}")
            ;;
        *)
            die "path must live under ${REPO_ROOT}: ${path}"
            ;;
    esac
}

host_abspath() {
    local path="$1"
    if [[ -d "${path}" ]]; then
        (
            cd "${path}"
            pwd
        )
        return
    fi

    (
        cd "$(dirname "${path}")"
        printf '%s/%s\n' "$(pwd)" "$(basename "${path}")"
    )
}

container_path_for() {
    local host_path="$1"
    ensure_repo_local_path "${host_path}"

    if [[ "${host_path}" == "${REPO_ROOT}" ]]; then
        printf '/workspace\n'
        return
    fi

    printf '/workspace/%s\n' "${host_path#"${REPO_ROOT}/"}"
}

pod_is_running() {
    podman pod exists "${POD_NAME}" >/dev/null 2>&1 || return 1
    [[ "$(podman pod inspect -f '{{.State}}' "${POD_NAME}")" == "Running" ]]
}

ensure_images() {
    local services=()
    local missing=()
    local service

    services=(
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

    for service in "${services[@]}"; do
        if ! podman image exists "$(image_name "${service}")"; then
            missing+=("${service}")
        fi
    done

    if [[ "${FORCE_BUILD}" -eq 0 && "${#missing[@]}" -eq 0 ]]; then
        return
    fi

    if [[ "${FORCE_BUILD}" -eq 1 ]]; then
        log 'building pod images before regression run'
    else
        log "building pod images because some are missing: ${missing[*]}"
    fi
    "${POD_SCRIPT}" build
}

ensure_pod() {
    if pod_is_running; then
        log "pod ${POD_NAME} is already running"
        return
    fi

    ensure_images

    log "starting pod ${POD_NAME}"
    "${POD_SCRIPT}" up
    pod_is_running || die "pod ${POD_NAME} failed to start"
}

open_report() {
    local report_path="$1"

    if command -v open >/dev/null 2>&1; then
        open "${report_path}" >/dev/null 2>&1 || true
        return
    fi
    if command -v xdg-open >/dev/null 2>&1; then
        xdg-open "${report_path}" >/dev/null 2>&1 || true
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -m webbrowser "file://${report_path}" >/dev/null 2>&1 || true
        return
    fi
    log "view requested but no opener found; report is at ${report_path}"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)
            FORCE_BUILD=1
            shift
            ;;
        --write-baseline)
            WRITE_BASELINE=1
            shift
            ;;
        --baseline)
            BASELINE_PATH="$2"
            shift 2
            ;;
        --fail-on-change)
            FAIL_ON_CHANGE=1
            shift
            ;;
        --view)
            VIEW_REPORT=1
            shift
            ;;
        --view-only)
            VIEW_REPORT=1
            VIEW_ONLY=1
            shift
            ;;
        --dataset-dir)
            DATASET_DIR="$2"
            shift 2
            ;;
        --output-dir)
            OUTPUT_DIR="$2"
            shift 2
            ;;
        --username)
            USERNAME="$2"
            shift 2
            ;;
        --password)
            PASSWORD="$2"
            shift 2
            ;;
        --proxy-url)
            PROXY_URL="$2"
            shift 2
            ;;
        --api-url)
            API_URL="$2"
            shift 2
            ;;
        --score-tolerance)
            SCORE_TOLERANCE="$2"
            shift 2
            ;;
        --audit-timeout)
            AUDIT_TIMEOUT="$2"
            shift 2
            ;;
        --poll-interval)
            POLL_INTERVAL="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            usage >&2
            die "unknown option: $1"
            ;;
    esac
done

require_command podman
podman info >/dev/null 2>&1 || die 'podman is installed but not reachable'
[[ -x "${POD_SCRIPT}" ]] || die "missing pod runner: ${POD_SCRIPT}"

mkdir -p "${OUTPUT_DIR}"
chmod 0777 "${OUTPUT_DIR}"
OUTPUT_DIR="$(host_abspath "${OUTPUT_DIR}")"
DATASET_DIR="$(host_abspath "${DATASET_DIR}")"
REPORT_PATH="${OUTPUT_DIR}/latest.html"

ensure_repo_local_path "${DATASET_DIR}"
ensure_repo_local_path "${OUTPUT_DIR}"

if [[ -n "${BASELINE_PATH}" ]]; then
    BASELINE_PATH="$(host_abspath "${BASELINE_PATH}")"
    ensure_repo_local_path "${BASELINE_PATH}"
fi

if [[ "${VIEW_ONLY}" -eq 1 ]]; then
    [[ -f "${REPORT_PATH}" ]] || die "no existing report to open: ${REPORT_PATH}"
    log "opening existing report ${REPORT_PATH}"
    open_report "${REPORT_PATH}"
    exit 0
fi

ensure_pod

run_cmd=(
    podman run
    --rm
    --pod "${POD_NAME}"
    --user "$(id -u):$(id -g)"
    -v "${REPO_ROOT}:/workspace"
    "$(image_name image-classifier)"
    python
    -m
    seenoevil_image_classifier.regression
    --dataset-dir "$(container_path_for "${DATASET_DIR}")"
    --output-dir "$(container_path_for "${OUTPUT_DIR}")"
    --proxy-url "${PROXY_URL}"
    --api-url "${API_URL}"
    --username "${USERNAME}"
    --password "${PASSWORD}"
)

if [[ -n "${BASELINE_PATH}" ]]; then
    run_cmd+=(--baseline "$(container_path_for "${BASELINE_PATH}")")
fi
if [[ "${WRITE_BASELINE}" -eq 1 ]]; then
    run_cmd+=(--write-baseline)
fi
if [[ "${FAIL_ON_CHANGE}" -eq 1 ]]; then
    run_cmd+=(--fail-on-change)
fi
if [[ -n "${SCORE_TOLERANCE}" ]]; then
    run_cmd+=(--score-tolerance "${SCORE_TOLERANCE}")
fi
if [[ -n "${AUDIT_TIMEOUT}" ]]; then
    run_cmd+=(--audit-timeout "${AUDIT_TIMEOUT}")
fi
if [[ -n "${POLL_INTERVAL}" ]]; then
    run_cmd+=(--poll-interval "${POLL_INTERVAL}")
fi

log "using dataset ${DATASET_DIR}"
log "writing regression artifacts to ${OUTPUT_DIR}"
"${run_cmd[@]}"

log "html report: ${REPORT_PATH}"
log "json report: ${OUTPUT_DIR}/latest.json"
if [[ "${VIEW_REPORT}" -eq 1 ]]; then
    open_report "${REPORT_PATH}"
fi
