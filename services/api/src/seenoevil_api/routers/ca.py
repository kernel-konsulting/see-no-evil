"""MITM root CA distribution endpoints.

Exposes the see-no-evil proxy's root certificate so client devices can fetch
and trust it without operators needing to copy files around manually.

The cert path is configurable via ``SEENOEVIL_MITM_CA_PATH`` (default
``/proxy-data/ca/ca.crt``).  In the standard pod / kube deployments the
proxy's data volume is bind-mounted read-only into the api container at
``/proxy-data``.

The cert itself is a public artefact (its private key is sealed inside the
proxy container), so download is intentionally **unauthenticated** — devices
on the LAN must be able to grab it before they have admin credentials.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response

CA_PATH_ENV = "SEENOEVIL_MITM_CA_PATH"
DEFAULT_CA_PATH = "/proxy-data/ca/ca.crt"


def _resolve_ca_path() -> Path:
    return Path(os.environ.get(CA_PATH_ENV, DEFAULT_CA_PATH))


def _safe_stat(path: Path) -> tuple[bool, int]:
    """Return (present, size). Treat permission errors as not-present."""
    try:
        st = path.stat()
    except (FileNotFoundError, PermissionError):
        return False, 0
    return True, st.st_size


def make_router() -> APIRouter:
    r = APIRouter(prefix="/v1/ca", tags=["ca"])

    @r.get("/cert", response_class=Response)
    def download_cert() -> Response:
        path = _resolve_ca_path()
        try:
            body = path.read_bytes()
        except (FileNotFoundError, PermissionError) as exc:
            raise HTTPException(
                status_code=404,
                detail=(
                    f"MITM root certificate not readable at {path}: {exc}. "
                    "The proxy may not have generated it yet, or the proxy "
                    "data volume is not mounted into the api container."
                ),
            ) from exc
        return Response(
            content=body,
            media_type="application/x-x509-ca-cert",
            headers={
                "Content-Disposition": 'attachment; filename="seenoevil-ca.crt"',
                "Cache-Control": "no-store",
            },
        )

    @r.get("/info")
    def cert_info() -> dict[str, object]:
        """Metadata about the MITM root cert plus per-platform install steps."""
        path = _resolve_ca_path()
        present, size = _safe_stat(path)
        return {
            "present": present,
            "path": str(path),
            "size_bytes": size,
            "download_url": "/v1/ca/cert",
            "install": {
                "macos": [
                    "Download seenoevil-ca.crt from the button above.",
                    "Double-click the file — Keychain Access opens.",
                    "Add to the 'System' keychain (admin password required).",
                    "Find 'see-no-evil MITM CA', double-click it, expand 'Trust',",
                    "and set 'When using this certificate' to 'Always Trust'.",
                ],
                "ios": [
                    "On the device, browse to https://<pod-ip>:<https-port>/v1/ca/cert",
                    "Safari prompts to download a Configuration Profile.",
                    "Settings → General → VPN & Device Management → install the profile.",
                    "Settings → General → About → Certificate Trust Settings →",
                    "enable full trust for 'see-no-evil MITM CA'.",
                ],
                "android": [
                    "Browse to the cert URL above; Android saves it to Downloads.",
                    "Settings → Security → Encryption & credentials →",
                    "Install a certificate → CA certificate → pick the file.",
                    "Confirm the security warning. The CA appears under",
                    "'User credentials'.",
                    "Note: Android 7+ ignores user CAs for app traffic — only",
                    "browser traffic and apps that explicitly opt in are filtered.",
                ],
                "windows": [
                    "Download the cert, then right-click → Install Certificate.",
                    "Choose 'Local Machine' → 'Place all certificates in the",
                    "following store' → Browse → 'Trusted Root Certification",
                    "Authorities' → Next → Finish.",
                    "Confirm the security warning.",
                ],
                "linux": [
                    "sudo cp seenoevil-ca.crt /usr/local/share/ca-certificates/",
                    "sudo update-ca-certificates",
                    "(Firefox uses its own NSS store — Preferences → Privacy &",
                    "Security → View Certificates → Authorities → Import.)",
                ],
            },
            "proxy_setup": {
                "summary": (
                    "After installing the CA, point the device's HTTP and HTTPS "
                    "proxy at the see-no-evil proxy port to enable content "
                    "classification."
                ),
                "macos": "System Settings → Network → Wi-Fi → Details → Proxies",
                "ios": "Settings → Wi-Fi → (i) on the network → Configure Proxy → Manual",
                "android": "Wi-Fi network details → Modify → Advanced → Proxy → Manual",
                "windows": "Settings → Network & Internet → Proxy → Manual proxy setup",
            },
        }

    return r
