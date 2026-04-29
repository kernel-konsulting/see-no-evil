package sampler_test

import (
	"bytes"
	"context"
	"errors"
	"image"
	"image/color"
	"image/jpeg"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"testing"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	classifyv1 "github.com/kernel-konsulting/see-no-evil/services/video-sampler/gen/classify/v1"
	"github.com/kernel-konsulting/see-no-evil/services/video-sampler/internal/sampler"
)

// fakeImageClassifier flags any frame containing the sentinel marker injected
// by makeJPEG when leadByte != 0.
type fakeImageClassifier struct {
	calls int
	last  []byte
}

var flagSentinel = []byte{0xFF, 0xFE, 0x00, 0x03, 0xFF}

func (f *fakeImageClassifier) Classify(_ context.Context, req *classifyv1.ClassifyImageRequest, _ ...grpc.CallOption) (*classifyv1.ClassifyImageResponse, error) {
	f.calls++
	f.last = req.ImageData
	action := classifyv1.Action_ACTION_ALLOW
	if bytes.Contains(req.ImageData, flagSentinel) {
		action = classifyv1.Action_ACTION_BLOCK
	}
	return &classifyv1.ClassifyImageResponse{
		Scores: []*classifyv1.Score{{Label: "porn", Value: 0.9}},
		Action: action,
		Reason: "fake",
	}, nil
}

type failingImageClassifier struct {
	calls int
}

func (f *failingImageClassifier) Classify(_ context.Context, _ *classifyv1.ClassifyImageRequest, _ ...grpc.CallOption) (*classifyv1.ClassifyImageResponse, error) {
	f.calls++
	return nil, errors.New("classifier unavailable")
}

// writeFakeFFmpeg drops a tiny shell script that ignores its inputs and
// produces N JPEG files matching the output pattern. Returns the script path.
func writeFakeFFmpeg(t *testing.T, dir string, frames int, payload []byte) string {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("fake ffmpeg uses POSIX shell")
	}
	script := filepath.Join(dir, "fake-ffmpeg.sh")
	// The script copies a fixed payload N times to the output template's
	// numbered slots. ffmpeg's pattern is "frame-%03d.jpg" so we emit
	// frame-001.jpg ... frame-N.jpg.
	contents := `#!/bin/sh
# Fake ffmpeg: ignores all flags except the trailing positional arg (the
# output pattern), and writes a fixed payload to N numbered files.
set -eu
PAYLOAD='` + filepath.Join(dir, "payload.jpg") + `'
PATTERN="$(eval echo \${$#})"  # last positional argument
DIR=$(dirname "$PATTERN")
mkdir -p "$DIR"
for i in $(seq 1 ` + itoaShell(frames) + `); do
  printf -v 'IDX' "%03d" "$i" 2>/dev/null || IDX=$(printf "%03d" "$i")
  cp "$PAYLOAD" "$DIR/frame-$IDX.jpg"
done
`
	if err := os.WriteFile(script, []byte(contents), 0o755); err != nil { //nolint:gosec
		t.Fatalf("write script: %v", err)
	}
	if err := os.WriteFile(filepath.Join(dir, "payload.jpg"), payload, 0o644); err != nil {
		t.Fatalf("write payload: %v", err)
	}
	return script
}

func itoaShell(i int) string {
	// Avoid pulling in strconv at package level just for the shell script.
	if i == 0 {
		return "0"
	}
	out := ""
	for i > 0 {
		out = string(rune('0'+i%10)) + out
		i /= 10
	}
	return out
}

// startGRPC stands the sampler up on a real loopback gRPC listener so we can
// exercise the streaming RPC end-to-end.
func startGRPC(t *testing.T, srv classifyv1.VideoSamplerServer) (classifyv1.VideoSamplerClient, func()) {
	t.Helper()
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("listen: %v", err)
	}
	gs := grpc.NewServer()
	classifyv1.RegisterVideoSamplerServer(gs, srv)
	go func() { _ = gs.Serve(lis) }()

	conn, err := grpc.NewClient(lis.Addr().String(), grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	cleanup := func() {
		_ = conn.Close()
		gs.GracefulStop()
	}
	return classifyv1.NewVideoSamplerClient(conn), cleanup
}

// makeJPEG produces a tiny valid JPEG with the given first byte after the SOI
// marker so the fake classifier can distinguish "block" vs "allow" payloads.
func makeJPEG(t *testing.T, leadByte byte) []byte {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, 16, 16))
	for y := 0; y < 16; y++ {
		for x := 0; x < 16; x++ {
			img.Set(x, y, color.RGBA{R: leadByte, G: leadByte, B: leadByte, A: 255})
		}
	}
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, img, &jpeg.Options{Quality: 60}); err != nil {
		t.Fatalf("encode: %v", err)
	}
	out := buf.Bytes()
	// Force the first byte the fake classifier sees to leadByte by prepending
	// a zero-length comment marker pair (FFFE0002 then leadByte).
	if leadByte != 0 {
		header := []byte{0xFF, 0xFE, 0x00, 0x03, leadByte}
		out = append(append([]byte{0xFF, 0xD8}, header...), out[2:]...)
	}
	return out
}

func TestSampleFramesBlockOnFlaggedFrame(t *testing.T) {
	tmp := t.TempDir()
	flaggedFrame := makeJPEG(t, 0xFF) // first byte non-zero → blocked by fake
	fake := writeFakeFFmpeg(t, tmp, 4, flaggedFrame)

	imgClient := &fakeImageClassifier{}
	srv := sampler.NewServer(sampler.Config{
		Image:         imgClient,
		DefaultFrames: 4,
		MaxVideoBytes: 1 << 20,
		FFmpegPath:    fake,
	})
	client, cleanup := startGRPC(t, srv)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream, err := client.Sample(ctx)
	if err != nil {
		t.Fatalf("open stream: %v", err)
	}
	if err := stream.Send(&classifyv1.SampleVideoRequest{
		RequestId: "req-1",
		MaxFrames: 4,
		Chunk:     bytes.Repeat([]byte("video"), 100),
	}); err != nil {
		t.Fatalf("send: %v", err)
	}
	resp, err := stream.CloseAndRecv()
	if err != nil {
		t.Fatalf("recv: %v", err)
	}

	if resp.Action != classifyv1.Action_ACTION_BLOCK {
		t.Errorf("expected BLOCK, got %s (reason=%q)", resp.Action, resp.Reason)
	}
	if resp.FramesScored != 4 {
		t.Errorf("expected 4 frames scored, got %d", resp.FramesScored)
	}
	if imgClient.calls != 4 {
		t.Errorf("expected 4 classifier calls, got %d", imgClient.calls)
	}
}

func TestSampleFramesAllowWhenNoneFlagged(t *testing.T) {
	tmp := t.TempDir()
	cleanFrame := makeJPEG(t, 0x00) // first byte zero → allowed
	fake := writeFakeFFmpeg(t, tmp, 3, cleanFrame)

	imgClient := &fakeImageClassifier{}
	srv := sampler.NewServer(sampler.Config{
		Image:         imgClient,
		DefaultFrames: 3,
		MaxVideoBytes: 1 << 20,
		FFmpegPath:    fake,
	})
	client, cleanup := startGRPC(t, srv)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream, err := client.Sample(ctx)
	if err != nil {
		t.Fatalf("open stream: %v", err)
	}
	_ = stream.Send(&classifyv1.SampleVideoRequest{RequestId: "req-2", Chunk: []byte("data")})
	resp, err := stream.CloseAndRecv()
	if err != nil {
		t.Fatalf("recv: %v", err)
	}

	if resp.Action != classifyv1.Action_ACTION_ALLOW {
		t.Errorf("expected ALLOW, got %s (reason=%q)", resp.Action, resp.Reason)
	}
	if resp.FramesScored != 3 {
		t.Errorf("expected 3 frames scored, got %d", resp.FramesScored)
	}
}

func TestSampleReportsFailureWhenNoFramesClassified(t *testing.T) {
	tmp := t.TempDir()
	cleanFrame := makeJPEG(t, 0x00)
	fake := writeFakeFFmpeg(t, tmp, 3, cleanFrame)

	imgClient := &failingImageClassifier{}
	srv := sampler.NewServer(sampler.Config{
		Image:         imgClient,
		DefaultFrames: 3,
		MaxVideoBytes: 1 << 20,
		FFmpegPath:    fake,
	})
	client, cleanup := startGRPC(t, srv)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream, err := client.Sample(ctx)
	if err != nil {
		t.Fatalf("open stream: %v", err)
	}
	_ = stream.Send(&classifyv1.SampleVideoRequest{RequestId: "req-fail", Chunk: []byte("data")})
	resp, err := stream.CloseAndRecv()
	if err != nil {
		t.Fatalf("recv: %v", err)
	}

	if resp.Action != classifyv1.Action_ACTION_ALLOW {
		t.Errorf("expected sampler to report allow with failure reason, got %s", resp.Action)
	}
	if resp.Reason != "video_sampler:classification_failed" {
		t.Errorf("unexpected reason: %q", resp.Reason)
	}
	if resp.FramesScored != 0 {
		t.Errorf("expected 0 frames scored, got %d", resp.FramesScored)
	}
	if imgClient.calls != 3 {
		t.Errorf("expected 3 classifier calls, got %d", imgClient.calls)
	}
}

func TestSampleFFmpegFailureReturnsAllow(t *testing.T) {
	imgClient := &fakeImageClassifier{}
	srv := sampler.NewServer(sampler.Config{
		Image:         imgClient,
		DefaultFrames: 2,
		MaxVideoBytes: 1 << 20,
		FFmpegPath:    "/nonexistent/ffmpeg-binary",
	})
	client, cleanup := startGRPC(t, srv)
	defer cleanup()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	stream, _ := client.Sample(ctx)
	_ = stream.Send(&classifyv1.SampleVideoRequest{RequestId: "req-3", Chunk: []byte("xxx")})
	resp, err := stream.CloseAndRecv()
	if err != nil {
		t.Fatalf("recv: %v", err)
	}
	if resp.Action != classifyv1.Action_ACTION_ALLOW {
		t.Errorf("expected fail-open ALLOW, got %s (reason=%q)", resp.Action, resp.Reason)
	}
	if resp.Reason != "video_sampler:ffmpeg_failed" {
		t.Errorf("unexpected reason: %q", resp.Reason)
	}
	if imgClient.calls != 0 {
		t.Errorf("expected no classifier calls when ffmpeg fails, got %d", imgClient.calls)
	}
}
