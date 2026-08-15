"""Decompression-bomb guards for the image preprocessing path."""

from __future__ import annotations

import io
import struct
import zlib

import pytest
from PIL import Image
from seenoevil_image_classifier.server import _MAX_IMAGE_PIXELS, _preprocess


def _png_chunk(typ: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + typ
        + data
        + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
    )


def _bomb_png(width: int, height: int) -> bytes:
    """A structurally valid PNG declaring huge dimensions but no pixel data."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(b"\x00" * 8))
        + _png_chunk(b"IEND", b"")
    )


def test_preprocess_rejects_decompression_bomb() -> None:
    raw = _bomb_png(100_000, 100_000)  # 10^10 pixels, far over the budget
    with pytest.raises(ValueError, match="image too large"):
        _preprocess(raw)


def test_preprocess_accepts_normal_image() -> None:
    img = Image.new("RGB", (64, 64), (10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    tensor = _preprocess(buf.getvalue())
    assert tensor.shape == (1, 3, 224, 224)


def test_pixel_budget_constant_sane() -> None:
    # 50 MP is comfortably above any real photo and far below OOM territory.
    assert _MAX_IMAGE_PIXELS == 50_000_000
