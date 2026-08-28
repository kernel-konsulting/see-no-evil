"""Catalogue integrity tests: no placeholder checksums, real SHA-256s.

The model weights are executed by onnxruntime on inspected content, so a
placeholder/empty checksum would silently disable supply-chain verification
(the exact bug this suite guards against).
"""

from __future__ import annotations

import re

from seenoevil_updater.updater import _LIST_ARTEFACTS, _MODEL_ARTEFACTS, _TEXT_ARTEFACTS

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ALL_MODEL_ARTEFACTS = [*_TEXT_ARTEFACTS, *[a for arts in _MODEL_ARTEFACTS.values() for a in arts]]


def test_model_artefacts_have_real_checksums() -> None:
    for art in ALL_MODEL_ARTEFACTS:
        assert _SHA256_RE.match(art.sha256), (
            f"{art.dest} has invalid/placeholder sha256 {art.sha256!r} — "
            "model downloads are unverifiable without a real checksum"
        )


def test_model_artefact_urls_are_https() -> None:
    for art in ALL_MODEL_ARTEFACTS:
        assert art.url.startswith("https://"), f"{art.dest} URL is not https: {art.url}"


def test_model_artefact_dests_unique() -> None:
    dests = [a.dest for a in ALL_MODEL_ARTEFACTS]
    assert len(dests) == len(set(dests)), f"duplicate dests: {dests}"


def test_known_variants_only() -> None:
    # The falconsai alias was removed: Falconsai publishes no ONNX export,
    # and the old entry silently downloaded the identical freepik file.
    assert set(_MODEL_ARTEFACTS) == {"freepik"}


def test_blocklists_allow_empty_checksum() -> None:
    # Blocklists are freshness-first; absence of a checksum there is a
    # documented tradeoff, not an oversight. Guard the intent explicitly so
    # nobody "fixes" it by deleting the check.
    for art in _LIST_ARTEFACTS:
        assert art.sha256 == ""
