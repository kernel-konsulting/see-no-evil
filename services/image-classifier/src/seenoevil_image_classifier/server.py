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
import pillow_avif  # noqa: F401  # registers AVIF support with Pillow
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from PIL import Image
from prometheus_client import Counter, Histogram, start_http_server

from .generated import classify_pb2, classify_pb2_grpc

try:
    from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]  # noqa: I001
except ImportError:
    _safe_fromstring = None  # type: ignore[assignment]

log = logging.getLogger("image-classifier")

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------

MODEL_PATH = Path(
    os.environ.get("IMAGE_CLASSIFIER_MODEL_PATH", "/data/models/image_classifier.onnx")
)
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
_MAX_SVG_BYTES = 1_000_000

# Reject images whose declared dimensions exceed this pixel budget *before*
# decoding. Pillow happily allocates the full grid, so a small crafted image
# with huge dimensions ("decompression bomb") could OOM the container.
_MAX_IMAGE_PIXELS = 50_000_000
Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS  # make Pillow raise, not warn


def _looks_like_svg(image_bytes: bytes) -> bool:
    head = image_bytes[:512].lstrip()
    return head.startswith(b"<svg") or b"<svg" in head[:256]


def _rasterize_svg(image_bytes: bytes) -> bytes:
    if len(image_bytes) > _MAX_SVG_BYTES:
        msg = f"SVG too large: {len(image_bytes)} bytes"
        raise ValueError(msg)

    if _safe_fromstring is None:
        raise ValueError("defusedxml required for SVG parsing (install defusedxml)")

    try:
        root = _safe_fromstring(image_bytes)
    except Exception as exc:  # defusedxml raises EntitiesForbidden, DTDForbidden, etc.
        # Normalize all XML parse/entity errors to ValueError for caller.
        msg = f"invalid SVG XML: {exc}"
        raise ValueError(msg) from exc

    if not root.tag.endswith("svg"):
        msg = "XML document is not an SVG"
        raise ValueError(msg)

    import cairosvg

    return cairosvg.svg2png(
        bytestring=image_bytes,
        output_width=_INPUT_SIZE[0],
        output_height=_INPUT_SIZE[1],
    )


def _preprocess(image_bytes: bytes) -> np.ndarray:
    """Convert raw image bytes → float32 NCHW tensor [1, 3, 224, 224]."""
    import io

    if _looks_like_svg(image_bytes):
        image_bytes = _rasterize_svg(image_bytes)

    try:
        img = Image.open(io.BytesIO(image_bytes))
    except Image.DecompressionBombError as exc:
        # Pillow's own bomb guard fired (dimensions > 2x MAX_IMAGE_PIXELS).
        raise ValueError(f"image too large (decompression bomb): {exc}") from exc
    # Decompression-bomb guard: check dimensions before any pixel work.
    w, h = img.size
    if w <= 0 or h <= 0 or int(w) * int(h) > _MAX_IMAGE_PIXELS:
        raise ValueError(f"image too large: {w}x{h} exceeds {_MAX_IMAGE_PIXELS} pixels")
    img = img.convert("RGB")
    img = img.resize(_INPUT_SIZE, Image.BILINEAR)
    arr = np.array(img, dtype=np.float32) / 255.0  # HWC [0,1]
    arr = arr.transpose(2, 0, 1)  # CHW
    return arr[np.newaxis, ...]  # NCHW


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
            # Common: SVG, ICO, AVIF, malformed/truncated, or HTML error pages
            # served with an image/* content-type. Log size + first bytes so the
            # operator can tell what the proxy actually sent us.
            head = bytes(request.image_data[:8])
            log.warning(
                "image decode failed: %s (size=%d, head=%s)",
                exc,
                len(request.image_data),
                head.hex(),
            )
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

        proto_scores = [classify_pb2.Score(label=k, value=v) for k, v in scores_map.items()]

        _classify_total.labels(action=classify_pb2.Action.Name(action)).inc()
        _classify_latency.observe(time.perf_counter() - t0)

        # Log every classification at DEBUG; log blocks at INFO so users can
        # see them at default verbosity. Set LOG_LEVEL=DEBUG to see all.
        top = sorted(scores_map.items(), key=lambda kv: kv[1], reverse=True)[:3]
        top_str = ", ".join(f"{k}={v:.3f}" for k, v in top)
        action_name = classify_pb2.Action.Name(action)
        if action == classify_pb2.ACTION_BLOCK:
            log.info(
                "image classify: %s reason=%s top=[%s] %dms",
                action_name,
                reason,
                top_str,
                latency_ms,
            )
        else:
            log.debug("image classify: %s top=[%s] %dms", action_name, top_str, latency_ms)

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
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    log.info("log level: %s", log_level)

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

    # Raise the default 4 MiB message caps so images up to the proxy's byte
    # cap reach us; otherwise large images fail with ResourceExhausted and
    # the proxy silently allows them unclassified.
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=WORKERS),
        options=[
            ("grpc.max_receive_message_length", 64 << 20),
            ("grpc.max_send_message_length", 64 << 20),
        ],
    )

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
