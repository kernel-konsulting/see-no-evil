"""Tests for the text-classifier service."""

from __future__ import annotations

from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture()
def mock_session():
    sess = MagicMock()
    # Two inputs: input_ids + attention_mask (no token_type_ids)
    sess.get_inputs.return_value = [
        MagicMock(name="input_ids"),
        MagicMock(name="attention_mask"),
    ]
    # Logits: slightly favouring SFW (index 0)
    sess.run.return_value = [np.array([[1.0, 0.1]], dtype=np.float32)]
    return sess


@pytest.fixture()
def mock_tokenizer():
    enc = MagicMock()
    enc.ids = [101, 2023, 2003, 1037, 3231, 102]
    enc.attention_mask = [1] * 6
    tok = MagicMock()
    tok.encode.return_value = enc
    return tok


def test_softmax_sums_to_one():
    from seenoevil_text_classifier.server import _softmax

    logits = np.array([1.0, 2.0, 3.0])
    probs = _softmax(logits)
    assert abs(probs.sum() - 1.0) < 1e-6


def test_action_allow_on_low_nsfw():
    from seenoevil_text_classifier import server
    from seenoevil_text_classifier.generated import classify_pb2

    action, reason = server._action_from_scores({"sfw": 0.9, "nsfw": 0.1})
    assert action == classify_pb2.ACTION_ALLOW


def test_action_block_on_high_nsfw():
    from seenoevil_text_classifier import server
    from seenoevil_text_classifier.generated import classify_pb2

    action, reason = server._action_from_scores({"sfw": 0.1, "nsfw": 0.9})
    assert action == classify_pb2.ACTION_BLOCK
    assert "nsfw" in reason


def test_classify_rpc_empty_text_returns_allow(mock_session, mock_tokenizer):
    from seenoevil_text_classifier import server
    from seenoevil_text_classifier.generated import classify_pb2

    servicer = server.TextClassifierServicer(mock_session, mock_tokenizer)
    request = classify_pb2.ClassifyTextRequest(text="   ", request_id="t-001")
    context = MagicMock()
    response = servicer.Classify(request, context)
    assert response.action == classify_pb2.ACTION_ALLOW


def test_classify_rpc_sfw_text(mock_session, mock_tokenizer):
    from seenoevil_text_classifier import server
    from seenoevil_text_classifier.generated import classify_pb2

    servicer = server.TextClassifierServicer(mock_session, mock_tokenizer)
    request = classify_pb2.ClassifyTextRequest(text="Hello world", request_id="t-002")
    context = MagicMock()
    response = servicer.Classify(request, context)
    # logits [1.0, 0.1] → softmax ≈ [0.71, 0.29] → nsfw < 0.5 → ALLOW
    assert response.action == classify_pb2.ACTION_ALLOW


def test_classify_rpc_inference_error(mock_session, mock_tokenizer):
    import grpc
    from seenoevil_text_classifier import server
    from seenoevil_text_classifier.generated import classify_pb2

    mock_session.run.side_effect = RuntimeError("boom")
    servicer = server.TextClassifierServicer(mock_session, mock_tokenizer)
    request = classify_pb2.ClassifyTextRequest(text="hello", request_id="t-003")
    context = MagicMock()
    servicer.Classify(request, context)
    context.set_code.assert_called_once_with(grpc.StatusCode.INTERNAL)
