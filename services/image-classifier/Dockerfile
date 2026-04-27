# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — image-classifier
#
# Multi-stage build:
#   builder  — installs Python deps + compiles protobuf stubs
#   runtime  — minimal image, non-root, no build tools
# -----------------------------------------------------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# Install grpcio-tools for protobuf code generation
RUN pip install --upgrade pip \
 && pip install grpcio-tools==1.67.1 protobuf==5.29.2

# Copy and install the service package
COPY services/image-classifier/pyproject.toml ./
COPY services/image-classifier/src/ ./src/
RUN pip install --prefix=/install ".[]"

# Generate gRPC stubs from the shared proto into the installed package
COPY shared/proto/classify.proto /proto/classify.proto
RUN python -m grpc_tools.protoc \
      -I/proto \
      --python_out=/install/lib/python3.12/site-packages/seenoevil_image_classifier/generated \
      --grpc_python_out=/install/lib/python3.12/site-packages/seenoevil_image_classifier/generated \
      /proto/classify.proto \
  && sed -i 's/^import classify_pb2/from . import classify_pb2/' \
       /install/lib/python3.12/site-packages/seenoevil_image_classifier/generated/classify_pb2_grpc.py

# -----------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/lib/python3.12/site-packages

RUN groupadd --system --gid 10001 app \
 && useradd  --system --uid 10001 --gid app --home /app --shell /sbin/nologin app

WORKDIR /app
COPY --from=builder /install /app

USER app

# gRPC service port
EXPOSE 50051
# Prometheus metrics port
EXPOSE 9101

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c \
      "import grpc; ch=grpc.insecure_channel('localhost:50051'); \
       from grpc_health.v1 import health_pb2,health_pb2_grpc; \
       s=health_pb2_grpc.HealthStub(ch); \
       r=s.Check(health_pb2.HealthCheckRequest(),timeout=3); \
       assert r.status==health_pb2.HealthCheckResponse.SERVING"

CMD ["python", "-m", "seenoevil_image_classifier"]
