"""Tests for the image-classifier service.

These tests mock the ONNX runtime session so no actual model file is needed.
The gRPC servicer is tested directly (no network) via unit-test-style calls.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import numpy as np
import pytest
from PIL import Image

# We import the module under test after patching onnxruntime so the import
# itself doesn't fail if onnxruntime is not installed in the test environment.


def _make_jpeg(width: int = 64, height: int = 64) -> bytes:
    """Return a minimal solid-colour JPEG."""
    img = Image.new("RGB", (width, height), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _make_svg() -> bytes:
    return b"""
        <svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">
            <rect width="64" height="64" fill="#6496c8"/>
            <circle cx="32" cy="32" r="18" fill="#ffffff"/>
        </svg>
        """


@pytest.fixture()
def mock_session():
    """Fake ONNX InferenceSession that returns all-zero logits."""
    sess = MagicMock()
    sess.get_inputs.return_value = [MagicMock(name="pixel_values")]
    # Shape: [1, 5] matching FREEPIK_LABELS
    sess.run.return_value = [np.zeros((1, 5), dtype=np.float32)]
    return sess


def test_preprocess_returns_nchw():
    from seenoevil_image_classifier.server import _preprocess

    tensor = _preprocess(_make_jpeg())
    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32
    assert tensor.min() >= 0.0
    assert tensor.max() <= 1.0


def test_preprocess_svg_returns_nchw():
    from seenoevil_image_classifier.server import _preprocess

    tensor = _preprocess(_make_svg())
    assert tensor.shape == (1, 3, 224, 224)
    assert tensor.dtype == np.float32
    assert tensor.min() >= 0.0
    assert tensor.max() <= 1.0


def test_action_allow_on_zero_scores():
    from seenoevil_image_classifier import server
    from seenoevil_image_classifier.generated import classify_pb2

    scores = {"drawings": 0.0, "hentai": 0.0, "neutral": 1.0, "porn": 0.0, "sexy": 0.0}
    action, reason = server._action_from_scores(scores)
    assert action == classify_pb2.ACTION_ALLOW
    assert reason == ""


def test_action_block_on_high_porn_score():
    from seenoevil_image_classifier import server
    from seenoevil_image_classifier.generated import classify_pb2

    scores = {"drawings": 0.0, "hentai": 0.0, "neutral": 0.0, "porn": 0.9, "sexy": 0.0}
    action, reason = server._action_from_scores(scores)
    assert action == classify_pb2.ACTION_BLOCK
    assert "porn" in reason


def test_classify_rpc_returns_allow(mock_session):
    from seenoevil_image_classifier import server
    from seenoevil_image_classifier.generated import classify_pb2

    servicer = server.ImageClassifierServicer(mock_session)
    request = classify_pb2.ClassifyImageRequest(
        image_data=_make_jpeg(),
        request_id="test-001",
    )
    context = MagicMock()
    response = servicer.Classify(request, context)

    assert response.action == classify_pb2.ACTION_ALLOW
    assert len(response.scores) == 5  # one per FREEPIK_LABELS entry
    assert response.latency_ms >= 0


def test_classify_rpc_bad_image_sets_grpc_error(mock_session):
    import grpc
    from seenoevil_image_classifier import server
    from seenoevil_image_classifier.generated import classify_pb2

    servicer = server.ImageClassifierServicer(mock_session)
    request = classify_pb2.ClassifyImageRequest(
        image_data=b"not an image",
        request_id="test-bad",
    )
    context = MagicMock()
    servicer.Classify(request, context)
    context.set_code.assert_called_once_with(grpc.StatusCode.INVALID_ARGUMENT)


def test_classify_rpc_inference_error_sets_grpc_error(mock_session):
    import grpc
    from seenoevil_image_classifier import server
    from seenoevil_image_classifier.generated import classify_pb2

    mock_session.run.side_effect = RuntimeError("ort error")
    servicer = server.ImageClassifierServicer(mock_session)
    request = classify_pb2.ClassifyImageRequest(
        image_data=_make_jpeg(),
        request_id="test-infer-err",
    )
    context = MagicMock()
    servicer.Classify(request, context)
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
