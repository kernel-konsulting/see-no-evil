"""Text classification gRPC service.

Loads the michellejieli/NSFW_text_classifier model (exported to ONNX) from
``TEXT_CLASSIFIER_MODEL_PATH`` and the accompanying tokenizer from
``TEXT_CLASSIFIER_TOKENIZER_PATH``.

Environment variables
---------------------
TEXT_CLASSIFIER_MODEL_PATH      Path to ONNX model (default /data/models/text_classifier.onnx).
TEXT_CLASSIFIER_TOKENIZER_PATH  Path to tokenizer.json (default /data/models/text_tokenizer.json).
TEXT_CLASSIFIER_DEVICE          cpu (default) | cuda | openvino.
TEXT_CLASSIFIER_PORT            gRPC port (default 50052).
TEXT_CLASSIFIER_WORKERS         Thread-pool size (default 4).
TEXT_CLASSIFIER_MAX_TOKENS      Max input tokens before truncation (default 512).
METRICS_PORT                    Prometheus port (default 9102).
"""

from __future__ import annotations

import logging
import os
import time
from concurrent import futures
from pathlib import Path

import grpc
import numpy as np
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from prometheus_client import Counter, Histogram, start_http_server

from .generated import classify_pb2, classify_pb2_grpc

log = logging.getLogger("text-classifier")

MODEL_PATH = Path(os.environ.get("TEXT_CLASSIFIER_MODEL_PATH", "/data/models/text_classifier.onnx"))
TOKENIZER_PATH = Path(
    os.environ.get("TEXT_CLASSIFIER_TOKENIZER_PATH", "/data/models/text_tokenizer.json")
)
DEVICE = os.environ.get("TEXT_CLASSIFIER_DEVICE", "cpu").lower()
PORT = int(os.environ.get("TEXT_CLASSIFIER_PORT", "50052"))
WORKERS = int(os.environ.get("TEXT_CLASSIFIER_WORKERS", "4"))
MAX_TOKENS = int(os.environ.get("TEXT_CLASSIFIER_MAX_TOKENS", "512"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9102"))

# Labels for michellejieli/NSFW_text_classifier (binary: NSFW / SFW).
# The model outputs logits for two classes; we take softmax and report both.
LABELS = ["sfw", "nsfw"]

_EP_MAP = {
    "cpu": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
}

_classify_total = Counter(
    "text_classifier_requests_total",
    "Total classification requests",
    ["action"],
)
_classify_latency = Histogram(
    "text_classifier_latency_seconds",
    "End-to-end classification latency",
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
)
_model_errors = Counter("text_classifier_model_errors_total", "Model inference errors")


def _softmax(x: np.ndarray) -> np.ndarray:
    e = np.exp(x - x.max())
    return e / e.sum()


def _load_session(model_path: Path, device: str):
    import onnxruntime as ort

    ep = _EP_MAP.get(device, "CPUExecutionProvider")
    available = ort.get_available_providers()
    if ep not in available:
        log.warning("EP %s unavailable, falling back to CPU", ep)
        ep = "CPUExecutionProvider"

    opts = ort.SessionOptions()
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(model_path), sess_options=opts, providers=[ep])


def _load_tokenizer(tokenizer_path: Path):
    from tokenizers import Tokenizer  # type: ignore[import-untyped]

    tok = Tokenizer.from_file(str(tokenizer_path))
    tok.enable_truncation(max_length=MAX_TOKENS)
    tok.enable_padding()
    return tok


_DEFAULT_NSFW_THRESHOLD = 0.5


def _action_from_scores(
    scores: dict[str, float],
    nsfw_threshold: float = _DEFAULT_NSFW_THRESHOLD,
) -> tuple[classify_pb2.Action, str]:  # type: ignore[name-defined]
    if scores.get("nsfw", 0.0) >= nsfw_threshold:
        return classify_pb2.ACTION_BLOCK, "text:nsfw"
    return classify_pb2.ACTION_ALLOW, ""


class TextClassifierServicer(classify_pb2_grpc.TextClassifierServicer):  # type: ignore[name-defined]
    def __init__(self, session, tokenizer) -> None:
        self._session = session
        self._tokenizer = tokenizer
        inputs = {inp.name for inp in session.get_inputs()}
        self._has_token_type_ids = "token_type_ids" in inputs

    def Classify(self, request, context):  # noqa: N802
        t0 = time.perf_counter()
        text = request.text.strip()
        if not text:
            return classify_pb2.ClassifyTextResponse(
                action=classify_pb2.ACTION_ALLOW,
                reason="empty input",
            )

        try:
            encoding = self._tokenizer.encode(text)
        except Exception as exc:
            log.warning("tokenisation failed: %s", exc)
            _model_errors.inc()
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"tokenisation error: {exc}")
            return classify_pb2.ClassifyTextResponse()

        input_ids = np.array([encoding.ids], dtype=np.int64)
        attention_mask = np.array([encoding.attention_mask], dtype=np.int64)
        feed: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if self._has_token_type_ids:
            feed["token_type_ids"] = np.zeros_like(input_ids)

        try:
            outputs = self._session.run(None, feed)
        except Exception as exc:
            log.error("inference failed: %s", exc, exc_info=True)
            _model_errors.inc()
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("inference error")
            return classify_pb2.ClassifyTextResponse()

        # outputs[0]: logits [1, num_labels]
        probs = _softmax(outputs[0][0]).tolist()
        scores_map = {label: float(probs[i]) for i, label in enumerate(LABELS[: len(probs)])}

        action, reason = _action_from_scores(scores_map)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        _classify_total.labels(action=classify_pb2.Action.Name(action)).inc()
        _classify_latency.observe(time.perf_counter() - t0)

        top = sorted(scores_map.items(), key=lambda kv: kv[1], reverse=True)[:3]
        top_str = ", ".join(f"{k}={v:.3f}" for k, v in top)
        action_name = classify_pb2.Action.Name(action)
        if action == classify_pb2.ACTION_BLOCK:
            log.info(
                "text classify: %s reason=%s top=[%s] %dms",
                action_name,
                reason,
                top_str,
                latency_ms,
            )
        else:
            log.debug("text classify: %s top=[%s] %dms", action_name, top_str, latency_ms)

        return classify_pb2.ClassifyTextResponse(
            scores=[classify_pb2.Score(label=k, value=v) for k, v in scores_map.items()],
            action=action,
            reason=reason,
            latency_ms=latency_ms,
        )


def serve() -> None:
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("log level: %s", log_level)

    for path, name in [(MODEL_PATH, "model"), (TOKENIZER_PATH, "tokenizer")]:
        if not path.exists():
            raise RuntimeError(
                f"{name.capitalize()} not found at {path}. Run the updater container first."
            )

    log.info("loading model from %s (device=%s)", MODEL_PATH, DEVICE)
    session = _load_session(MODEL_PATH, DEVICE)
    log.info("loading tokenizer from %s", TOKENIZER_PATH)
    tokenizer = _load_tokenizer(TOKENIZER_PATH)

    start_http_server(METRICS_PORT)
    log.info("prometheus metrics on :%d", METRICS_PORT)

    # Raise the default 4 MiB message caps (parity with the proxy's client).
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=WORKERS),
        options=[
            ("grpc.max_receive_message_length", 64 << 20),
            ("grpc.max_send_message_length", 64 << 20),
        ],
    )
    classify_pb2_grpc.add_TextClassifierServicer_to_server(
        TextClassifierServicer(session, tokenizer), server
    )

    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set(
        "seenoevil.classify.v1.TextClassifier",
        health_pb2.HealthCheckResponse.SERVING,
    )

    listen_addr = f"[::]:{PORT}"
    server.add_insecure_port(listen_addr)
    server.start()
    log.info("listening on %s", listen_addr)
    server.wait_for_termination()
