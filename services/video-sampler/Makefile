# see-no-evil video-sampler — developer Makefile

PROTO_SRC := ../../shared/proto/classify.proto
GEN_DIR   := gen/classify/v1

.PHONY: generate build test lint tidy

generate:
	mkdir -p $(GEN_DIR)
	protoc \
	  -I../../shared/proto \
	  --go_out=$(GEN_DIR) \
	  --go_opt=paths=source_relative \
	  --go-grpc_out=$(GEN_DIR) \
	  --go-grpc_opt=paths=source_relative \
	  $(PROTO_SRC)

build: generate
	go build -trimpath ./...

test: generate
	go test ./...

lint:
	golangci-lint run ./...

tidy:
	go mod tidy
