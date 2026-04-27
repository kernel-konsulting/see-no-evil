# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — proxy data-plane (Go)
#
# Multi-stage build:
#   proto   — generate gRPC stubs from classify.proto
#   builder — compile the Go binary
#   runtime — minimal distroless image
# -----------------------------------------------------------------------------

# ── Proto generation ─────────────────────────────────────────────────────────
FROM golang:1.23-bookworm AS proto

RUN apt-get update -qq && apt-get install -y -q --no-install-recommends \
      protobuf-compiler \
 && rm -rf /var/lib/apt/lists/*

RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.0 \
 && go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1

WORKDIR /src
COPY shared/proto/classify.proto /proto/classify.proto

# Generate into gen/classify/v1/ so the import path matches go.mod.
RUN mkdir -p gen/classify/v1 \
 && protoc \
      -I/proto \
      --go_out=gen/classify/v1 \
      --go_opt=paths=source_relative \
      --go-grpc_out=gen/classify/v1 \
      --go-grpc_opt=paths=source_relative \
      /proto/classify.proto

# ── Go build ─────────────────────────────────────────────────────────────────
FROM golang:1.23-bookworm AS builder

ENV CGO_ENABLED=0 \
    GOOS=linux

WORKDIR /src

# Copy module files first for layer caching.
COPY services/proxy/go.mod services/proxy/go.sum* ./
RUN go mod download

# Copy generated stubs + source.
COPY --from=proto /src/gen ./gen
COPY services/proxy/ .

RUN go build -trimpath -ldflags="-s -w" -o /proxy ./...

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM gcr.io/distroless/static-debian12:nonroot AS runtime

COPY --from=builder /proxy /proxy

# HTTP proxy port (clients point their proxy settings here)
EXPOSE 8080
# Prometheus metrics
EXPOSE 9100

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["/proxy", "-healthcheck"]

ENTRYPOINT ["/proxy"]
