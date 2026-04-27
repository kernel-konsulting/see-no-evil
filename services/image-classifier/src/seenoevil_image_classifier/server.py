"""Image classification gRPC service.

Loads a Freepik nsfw_image_detector (or Falconsai) ONNX model from the path
given by ``IMAGE_CLASSIFIER_MODEL_PATH`` (default ``/data/models/image_classifier.onnx``)
and serves the ``ImageClassifier`` gRPC service defined in ``classify.proto``.

The model must be present before the service starts.  The ``updater`` container
is responsible for fetching and verifying it on first start.

Environment variables
---------------------
IMAGE_CLASSIFIER_MODEL_PATH   Path to the ONNX model file.
IMAGE_CLASSIFIER_DEVICE       Execution provider: cpu (default), cuda, openvino, coreml.
IMAGE_CLASSIFIER_PORT         gRPC listen port (default 50051).
IMAGE_CLASSIFIER_WORKERS      Number of gRPC thread-pool workers (default 4).
IMAGE_CLASSIFIER_BATCH_SIZE   Max images per inference batch (default 8).
METRICS_PORT                  Prometheus /metrics HTTP port (default 9101).
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
from PIL import Image
from prometheus_client import Counter, Histogram, start_http_server

from .generated import classify_pb2, classify_pb2_grpc

log = logging.getLogger("image-classifier")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

MODEL_PATH = Path(os.environ.get("IMAGE_CLASSIFIER_MODEL_PATH", "/data/models/image_classifier.onnx"))
DEVICE = os.environ.get("IMAGE_CLASSIFIER_DEVICE", "cpu").lower()
PORT = int(os.environ.get("IMAGE_CLASSIFIER_PORT", "50051"))
WORKERS = int(os.environ.get("IMAGE_CLASSIFIER_WORKERS", "4"))
METRICS_PORT = int(os.environ.get("METRICS_PORT", "9101"))

# Labels produced by the Freepik nsfw_image_detector model (in inference order).
# Falconsai uses the same label set.
FREEPIK_LABELS = [
    "drawings",
    "hentai",
    "neutral",
    "porn",
    "sexy",
]

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_classify_total = Counter(
    "image_classifier_requests_total",
    "Total classification requests",
    ["action"],
)
_classify_latency = Histogram(
    "image_classifier_latency_seconds",
    "End-to-end classification latency",
    buckets=[0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
_model_errors = Counter("image_classifier_model_errors_total", "Model inference errors")

# ---------------------------------------------------------------------------
# Execution-provider mapping
# ---------------------------------------------------------------------------

_EP_MAP = {
    "cpu": "CPUExecutionProvider",
    "cuda": "CUDAExecutionProvider",
    "openvino": "OpenVINOExecutionProvider",
    "coreml": "CoreMLExecutionProvider",
}


def _load_session(model_path: Path, device: str):
    """Load ONNX Runtime InferenceSession with the requested EP."""
    import onnxruntime as ort  # local import so tests can mock it

    ep = _EP_MAP.get(device, "CPUExecutionProvider")
    available = ort.get_available_providers()
    if ep not in available:
        log.warning("EP %s not available (have %s), falling back to CPU", ep, available)
        ep = "CPUExecutionProvider"

    opts = ort.SessionOptions()
    opts.enable_mem_pattern = True
    opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    return ort.InferenceSession(str(model_path), sess_options=opts, providers=[ep])


# ---------------------------------------------------------------------------
# Image preprocessing
# ---------------------------------------------------------------------------

# Input shape expected by the Freepik model: 224×224 RGB, normalised to [0,1].
_INPUT_SIZE = (224, 224)


def _preprocess(image_bytes: bytes) -> np.ndarray:
    """Convert raw image bytes → float32 NCHW tensor [1, 3, 224, 224]."""
    import io
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    img = img.resize(_INPUT_SIZE, Image.BILINEAR)
    arr = np.array(img, dtype=np.float32) / 255.0          # HWC [0,1]
    arr = arr.transpose(2, 0, 1)                            # CHW
    return arr[np.newaxis, ...]                             # NCHW


# ---------------------------------------------------------------------------
# gRPC service implementation
# ---------------------------------------------------------------------------

_DEFAULT_THRESHOLDS: dict[str, float] = {
    "hentai": 0.5,
    "porn": 0.5,
    "sexy": 0.8,
}


def _action_from_scores(
    scores: dict[str, float],
    thresholds: dict[str, float] | None = None,
) -> tuple[classify_pb2.Action, str]:  # type: ignore[name-defined]
    t = thresholds or _DEFAULT_THRESHOLDS
    for label, threshold in t.items():
        if scores.get(label, 0.0) >= threshold:
            return classify_pb2.ACTION_BLOCK, f"image:{label}"
    return classify_pb2.ACTION_ALLOW, ""


class ImageClassifierServicer(classify_pb2_grpc.ImageClassifierServicer):  # type: ignore[name-defined]
    def __init__(self, session) -> None:
        self._session = session
        self._input_name: str = session.get_inputs()[0].name

    def Classify(self, request, context):  # noqa: N802
        t0 = time.perf_counter()
        try:
            tensor = _preprocess(request.image_data)
        except Exception as exc:
            log.warning("image decode failed: %s", exc)
            _model_errors.inc()
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(f"image decode error: {exc}")
            return classify_pb2.ClassifyImageResponse()

        try:
            outputs = self._session.run(None, {self._input_name: tensor})
        except Exception as exc:
            log.error("model inference failed: %s", exc, exc_info=True)
            _model_errors.inc()
            context.set_code(grpc.StatusCode.INTERNAL)
            context.set_details("inference error")
            return classify_pb2.ClassifyImageResponse()

        # outputs[0] shape: [1, num_labels]
        raw: np.ndarray = outputs[0][0]
        label_names: list[str] = FREEPIK_LABELS[: len(raw)]
        scores_map = {label: float(raw[i]) for i, label in enumerate(label_names)}

        action, reason = _action_from_scores(scores_map)
        latency_ms = int((time.perf_counter() - t0) * 1000)

        proto_scores = [
            classify_pb2.Score(label=k, value=v) for k, v in scores_map.items()
        ]

        _classify_total.labels(action=classify_pb2.Action.Name(action)).inc()
        _classify_latency.observe((time.perf_counter() - t0))

        return classify_pb2.ClassifyImageResponse(
            scores=proto_scores,
            action=action,
            reason=reason,
            latency_ms=latency_ms,
        )


# ---------------------------------------------------------------------------
# Server entrypoint
# ---------------------------------------------------------------------------

def serve() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if not MODEL_PATH.exists():
        raise RuntimeError(
            f"Model not found at {MODEL_PATH}. "
            "Run the updater container first to fetch model weights."
        )

    log.info("loading model from %s (device=%s)", MODEL_PATH, DEVICE)
    session = _load_session(MODEL_PATH, DEVICE)
    log.info("model loaded; input name=%s", session.get_inputs()[0].name)

    start_http_server(METRICS_PORT)
    log.info("prometheus metrics on :%d", METRICS_PORT)

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=WORKERS))

    classify_pb2_grpc.add_ImageClassifierServicer_to_server(
        ImageClassifierServicer(session), server
    )

    health_servicer = health.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set(
        "seenoevil.classify.v1.ImageClassifier",
        health_pb2.HealthCheckResponse.SERVING,
    )

    listen_addr = f"[::]:{PORT}"
    server.add_insecure_port(listen_addr)
    server.start()
    log.info("listening on %s", listen_addr)
    server.wait_for_termination()
