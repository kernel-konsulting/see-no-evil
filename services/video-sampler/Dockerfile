# syntax=docker/dockerfile:1.7
# -----------------------------------------------------------------------------
# see-no-evil — video-sampler (M5)
#
# Multi-stage build mirroring services/proxy/Dockerfile:
#   proto   — generate gRPC stubs from classify.proto
#   builder — compile the Go binary
#   runtime — alpine + ffmpeg
# -----------------------------------------------------------------------------

# ── Proto generation ─────────────────────────────────────────────────────────
FROM golang:1.23-bookworm AS proto

RUN apt-get update -qq && apt-get install -y -q --no-install-recommends \
      protobuf-compiler \
 && rm -rf /var/lib/apt/lists/*

RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.0 \
 && go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1

WORKDIR /src
COPY ../../shared/proto/classify.proto /proto/classify.proto

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

COPY go.mod go.sum* ./
RUN go mod download

COPY --from=proto /src/gen ./gen
COPY . .

RUN go build -trimpath -ldflags="-s -w" -o /video-sampler .

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM alpine:3.20 AS runtime

RUN apk add --no-cache ffmpeg ca-certificates \
 && addgroup -S -g 10001 app \
 && adduser -S -G app -u 10001 -h /app -s /sbin/nologin app

WORKDIR /app
COPY --from=builder /video-sampler /usr/local/bin/video-sampler

USER app
EXPOSE 50053 9103

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["/usr/local/bin/video-sampler", "-healthcheck"]

ENTRYPOINT ["/usr/local/bin/video-sampler"]
