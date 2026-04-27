# see-no-evil proxy — developer Makefile
#
# Prerequisites: Go 1.23+, protoc, protoc-gen-go, protoc-gen-go-grpc
#   brew install protobuf
#   go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.0
#   go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@v1.5.1

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
