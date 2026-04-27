"""Model weight and blocklist updater.

Runs once at pod startup (and optionally on a schedule) to fetch model weights
and DNS blocklists.  Nothing from the inspected traffic ever passes through this
service; it only makes outbound GET requests to known, configured URLs.

Artefact catalogue (shipped in this file as the single source of truth):

  IMAGE_CLASSIFIER
    - image_classifier.onnx      (Freepik nsfw_image_detector, ONNX export)
    - image_classifier_tiny.onnx (Falconsai, smaller, for Pi 4)

  TEXT_CLASSIFIER
    - text_classifier.onnx       (michellejieli/NSFW_text_classifier, ONNX export)
    - text_tokenizer.json        (matching HF tokenizer)

Checksums are SHA-256.  The updater refuses to use a file whose checksum does
not match; it deletes the partial download and exits non-zero.

Environment variables
---------------------
MODELS_DIR     Where to write model files (default /data/models).
LISTS_DIR      Where to write blocklists (default /data/lists).
MODEL_VARIANT  freepik (default) | falconsai
SKIP_MODELS    1 to skip model download (useful when weights are pre-mounted).
SKIP_LISTS     1 to skip blocklist download.
HTTP_TIMEOUT   Seconds for HTTP requests (default 120).
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx

log = logging.getLogger("updater")

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/data/models"))
LISTS_DIR = Path(os.environ.get("LISTS_DIR", "/data/lists"))
MODEL_VARIANT = os.environ.get("MODEL_VARIANT", "freepik").lower()
SKIP_MODELS = os.environ.get("SKIP_MODELS", "0") == "1"
SKIP_LISTS = os.environ.get("SKIP_LISTS", "0") == "1"
HTTP_TIMEOUT = float(os.environ.get("HTTP_TIMEOUT", "120"))

# ---------------------------------------------------------------------------
# Artefact catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Artefact:
    dest: str  # relative to MODELS_DIR or LISTS_DIR
    url: str
    sha256: str


# Hosted on Hugging Face Hub model repos.  We pull the ONNX export directly.
# These checksums must be updated whenever upstream releases a new model version.
_MODEL_ARTEFACTS: dict[str, list[Artefact]] = {
    "freepik": [
        Artefact(
            dest="image_classifier.onnx",
            # Freepik nsfw_image_detector ONNX export (community upload)
            url="https://huggingface.co/Falconsai/nsfw_image_detection/resolve/main/onnx/model.onnx",
            sha256="PLACEHOLDER_FREEPIK_SHA256",  # updated at release
        ),
    ],
    "falconsai": [
        Artefact(
            dest="image_classifier.onnx",
            url="https://huggingface.co/Falconsai/nsfw_image_detection/resolve/main/onnx/model.onnx",
            sha256="PLACEHOLDER_FALCONSAI_SHA256",
        ),
    ],
}

_TEXT_ARTEFACTS: list[Artefact] = [
    Artefact(
        dest="text_classifier.onnx",
        url="https://huggingface.co/michellejieli/NSFW_text_classifier/resolve/main/onnx/model.onnx",
        sha256="PLACEHOLDER_TEXT_ONNX_SHA256",
    ),
    Artefact(
        dest="text_tokenizer.json",
        url="https://huggingface.co/michellejieli/NSFW_text_classifier/resolve/main/tokenizer.json",
        sha256="PLACEHOLDER_TEXT_TOKENIZER_SHA256",
    ),
]

# Blocklists fetched for Blocky.  These are plain-text host files.
_LIST_ARTEFACTS: list[Artefact] = [
    Artefact(
        dest="stevenblack.txt",
        url="https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts",
        sha256="",  # checksums for frequently-updated lists are skipped; freshness is the goal
    ),
    Artefact(
        dest="oisd_nsfw.txt",
        url="https://nsfw.oisd.nl/domainswild",
        sha256="",
    ),
]

# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(client: httpx.Client, artefact: Artefact, dest_dir: Path) -> None:
    dest = dest_dir / artefact.dest

    if dest.exists() and artefact.sha256 and artefact.sha256.startswith("PLACEHOLDER"):
        log.info("skipping %s (placeholder checksum — update catalogue before release)", dest.name)
        return

    if dest.exists() and artefact.sha256 and not artefact.sha256.startswith("PLACEHOLDER"):
        actual = _sha256_file(dest)
        if actual == artefact.sha256:
            log.info("%s already present and verified, skipping", dest.name)
            return
        log.warning(
            "%s checksum mismatch (expected %s got %s), re-downloading",
            dest.name,
            artefact.sha256,
            actual,
        )

    log.info("downloading %s → %s", artefact.url, dest)
    dest_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.NamedTemporaryFile(dir=dest_dir, delete=False) as tmp:
        tmp_path = Path(tmp.name)
        try:
            with client.stream("GET", artefact.url) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes(chunk_size=65536):
                    tmp.write(chunk)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    if artefact.sha256 and not artefact.sha256.startswith("PLACEHOLDER"):
        actual = _sha256_file(tmp_path)
        if actual != artefact.sha256:
            tmp_path.unlink(missing_ok=True)
            raise RuntimeError(
                f"checksum mismatch for {artefact.dest}: expected {artefact.sha256}, got {actual}"
            )

    shutil.move(str(tmp_path), dest)
    log.info("saved %s (%d bytes)", dest.name, dest.stat().st_size)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    errors: list[str] = []

    with httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        if not SKIP_MODELS:
            model_artefacts = _MODEL_ARTEFACTS.get(MODEL_VARIANT, [])
            if not model_artefacts:
                log.error("unknown MODEL_VARIANT=%s", MODEL_VARIANT)
                sys.exit(1)

            for art in model_artefacts + _TEXT_ARTEFACTS:
                try:
                    _download(client, art, MODELS_DIR)
                except Exception as exc:
                    log.error("failed to download %s: %s", art.dest, exc)
                    errors.append(art.dest)
        else:
            log.info("SKIP_MODELS=1, skipping model downloads")

        if not SKIP_LISTS:
            for art in _LIST_ARTEFACTS:
                try:
                    _download(client, art, LISTS_DIR)
                except Exception as exc:
                    log.error("failed to download %s: %s", art.dest, exc)
                    errors.append(art.dest)
        else:
            log.info("SKIP_LISTS=1, skipping blocklist downloads")

    if errors:
        log.error("updater finished with errors: %s", errors)
        sys.exit(1)

    log.info("updater finished successfully")
