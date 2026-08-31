// Package sampler implements the see-no-evil video frame sampler. It accepts
// streamed video bytes, extracts evenly-spaced frames with ffmpeg, classifies
// each frame with the image classifier, and reports a worst-case verdict plus
// a thumbnail (the most-offensive frame) for the quarantine UI.
package sampler

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"

	classifyv1 "github.com/kernel-konsulting/see-no-evil/services/video-sampler/gen/classify/v1"
)

// ---------------------------------------------------------------------------
// Metrics
// ---------------------------------------------------------------------------

var (
	samplesTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "video_sampler_requests_total",
		Help: "Total Sample() RPC calls",
	}, []string{"action"})

	frameLatency = promauto.NewHistogram(prometheus.HistogramOpts{
		Name:    "video_sampler_frame_latency_seconds",
		Help:    "Per-frame classification latency",
		Buckets: prometheus.ExponentialBuckets(0.01, 2, 10),
	})

	ffmpegErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "video_sampler_ffmpeg_errors_total",
		Help: "ffmpeg invocations that exited non-zero",
	})

	bytesTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "video_sampler_bytes_total",
		Help: "Total bytes received over Sample() streams",
	})
)

// ---------------------------------------------------------------------------
// Config + Server
// ---------------------------------------------------------------------------

// Config holds dependencies and tunables for the video sampler.
type Config struct {
	// Image is the gRPC client for the image classifier service.
	Image classifyv1.ImageClassifierClient
	// DefaultFrames is the number of evenly-spaced frames to extract when the
	// caller doesn't override max_frames. Falls back to 8 when zero.
	DefaultFrames int
	// MaxVideoBytes is the cap on the buffered video size. Streams exceeding
	// this limit are truncated; analysis runs against whatever was buffered.
	MaxVideoBytes int64
	// FFmpegPath is the executable to invoke (default "ffmpeg"). Override in
	// tests to point at a fake script.
	FFmpegPath string
	// FFprobePath is used to probe the video duration so frames can be sampled
	// evenly across the whole timeline (default "ffprobe").
	FFprobePath string
	// ThumbnailWidth scales the worst-frame thumbnail down before storage.
	ThumbnailWidth int
	// ThumbnailJPEGQ sets the JPEG quality of the thumbnail (1..31, lower is
	// higher quality with libjpeg's qscale).
	ThumbnailJPEGQ int
	// MaxConcurrent bounds how many Sample streams run at once. ffmpeg is
	// CPU/disk hungry and each stream buffers up to MaxVideoBytes on disk, so
	// unbounded concurrency is a DoS vector. 0 → default 2.
	MaxConcurrent int
}

// Server implements VideoSamplerServer.
type Server struct {
	classifyv1.UnimplementedVideoSamplerServer
	cfg Config
	sem chan struct{}
}

func NewServer(cfg Config) *Server {
	if cfg.DefaultFrames <= 0 {
		cfg.DefaultFrames = 8
	}
	if cfg.MaxVideoBytes <= 0 {
		cfg.MaxVideoBytes = 500 << 20
	}
	if cfg.FFmpegPath == "" {
		cfg.FFmpegPath = "ffmpeg"
	}
	if cfg.FFprobePath == "" {
		cfg.FFprobePath = "ffprobe"
	}
	if cfg.ThumbnailWidth <= 0 {
		cfg.ThumbnailWidth = 256
	}
	if cfg.ThumbnailJPEGQ <= 0 {
		cfg.ThumbnailJPEGQ = 60
	}
	if cfg.MaxConcurrent <= 0 {
		cfg.MaxConcurrent = 2
	}
	return &Server{cfg: cfg, sem: make(chan struct{}, cfg.MaxConcurrent)}
}

// Sample implements the streaming RPC.
func (s *Server) Sample(stream classifyv1.VideoSampler_SampleServer) error {
	t0 := time.Now()

	// Admission control: refuse (rather than queue) when the sampler is at
	// capacity. The proxy treats this as a sampler failure and, by default,
	// fails open — a busy sampler must not stall video streaming.
	select {
	case s.sem <- struct{}{}:
		defer func() { <-s.sem }()
	default:
		return status.Error(codes.ResourceExhausted, "video_sampler:busy")
	}

	tmpDir, err := os.MkdirTemp("", "vsampler-")
	if err != nil {
		return fmt.Errorf("mkdtemp: %w", err)
	}
	defer func() { _ = os.RemoveAll(tmpDir) }()

	videoPath := filepath.Join(tmpDir, "in.bin")
	requestID, frames, written, err := receiveStream(stream, videoPath, s.cfg.MaxVideoBytes)
	if err != nil {
		return err
	}
	bytesTotal.Add(float64(written))

	if frames <= 0 {
		frames = s.cfg.DefaultFrames
	}

	imgs, err := s.extractFrames(stream.Context(), videoPath, tmpDir, frames)
	if err != nil {
		ffmpegErrors.Inc()
		slog.Warn("ffmpeg extraction failed", "err", err, "request_id", requestID)
		// Fail open: if we can't sample frames the proxy must allow the video.
		samplesTotal.WithLabelValues("error").Inc()
		return stream.SendAndClose(&classifyv1.SampleVideoResponse{
			Action:    classifyv1.Action_ACTION_ALLOW,
			Reason:    "video_sampler:ffmpeg_failed",
			LatencyMs: time.Since(t0).Milliseconds(),
		})
	}

	worst, scored, action, reason := s.classifyFrames(stream.Context(), imgs, requestID)

	resp := &classifyv1.SampleVideoResponse{
		WorstScores:  worst,
		Action:       action,
		Reason:       reason,
		FramesScored: int32(scored),
		LatencyMs:    time.Since(t0).Milliseconds(),
	}
	samplesTotal.WithLabelValues(action.String()).Inc()
	return stream.SendAndClose(resp)
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

// receiveStream drains the incoming gRPC stream into dest, capping at maxBytes.
// Returns the request_id from the first non-empty message and the requested
// max_frames (if any).
func receiveStream(stream classifyv1.VideoSampler_SampleServer, dest string, maxBytes int64) (string, int, int64, error) {
	f, err := os.Create(dest)
	if err != nil {
		return "", 0, 0, fmt.Errorf("create %s: %w", dest, err)
	}
	defer func() { _ = f.Close() }()

	var (
		requestID string
		frames    int
		written   int64
		truncated bool
	)
	for {
		msg, err := stream.Recv()
		if err == io.EOF {
			break
		}
		if err != nil {
			return "", 0, 0, fmt.Errorf("recv: %w", err)
		}
		if requestID == "" && msg.RequestId != "" {
			requestID = msg.RequestId
		}
		if frames == 0 && msg.MaxFrames > 0 {
			frames = int(msg.MaxFrames)
		}
		if truncated || len(msg.Chunk) == 0 {
			continue
		}
		remaining := maxBytes - written
		if remaining <= 0 {
			truncated = true
			continue
		}
		chunk := msg.Chunk
		if int64(len(chunk)) > remaining {
			chunk = chunk[:remaining]
			truncated = true
		}
		n, werr := f.Write(chunk)
		if werr != nil {
			return "", 0, 0, fmt.Errorf("write: %w", werr)
		}
		written += int64(n)
	}
	return requestID, frames, written, nil
}

// extractFrames runs ffmpeg to pull `count` evenly-spaced JPEG frames from
// `videoPath` into `dir`. Returns the absolute paths of the extracted frames.
//
// Strategy: probe the duration with ffprobe and ask ffmpeg for an effective
// frame rate of `count/duration` so the output is exactly `count` frames
// evenly spread across the whole video. Falls back to the legacy `thumbnail`
// filter when ffprobe fails (e.g. truncated stream with no moov atom).
func (s *Server) extractFrames(ctx context.Context, videoPath, dir string, count int) ([]string, error) {
	if count <= 0 {
		count = s.cfg.DefaultFrames
	}
	pattern := filepath.Join(dir, "frame-%03d.jpg")

	dur, probeErr := s.probeDuration(ctx, videoPath)
	var vf string
	switch {
	case probeErr == nil && dur > 0.5:
		// fps = count / duration places one frame every duration/count seconds.
		// Cap fps at 30 in case the video is very short.
		fps := float64(count) / dur
		if fps > 30 {
			fps = 30
		}
		vf = fmt.Sprintf("fps=%.6f,scale=%d:-1", fps, s.cfg.ThumbnailWidth)
	default:
		// Fallback: pick representative frames from each scene change. Not
		// evenly-spaced but at least diverse.
		vf = "thumbnail,scale=" + strconv.Itoa(s.cfg.ThumbnailWidth) + ":-1"
		if probeErr != nil {
			slog.Debug("ffprobe failed; using thumbnail filter", "err", probeErr)
		}
	}

	args := []string{
		"-hide_banner",
		"-loglevel", "error",
		"-y",
		"-i", videoPath,
		"-vf", vf,
		"-frames:v", strconv.Itoa(count),
		"-q:v", strconv.Itoa(s.cfg.ThumbnailJPEGQ),
		pattern,
	}
	cmd := exec.CommandContext(ctx, s.cfg.FFmpegPath, args...) //nolint:gosec // FFmpegPath is admin-controlled
	out, err := cmd.CombinedOutput()
	if err != nil {
		return nil, fmt.Errorf("ffmpeg: %w (stderr: %s)", err, string(out))
	}

	matches, err := filepath.Glob(filepath.Join(dir, "frame-*.jpg"))
	if err != nil {
		return nil, err
	}
	sort.Strings(matches)
	return matches, nil
}

// probeDuration returns the video duration in seconds via ffprobe.
func (s *Server) probeDuration(ctx context.Context, videoPath string) (float64, error) {
	cmd := exec.CommandContext(ctx, s.cfg.FFprobePath, //nolint:gosec // FFprobePath is admin-controlled
		"-v", "error",
		"-show_entries", "format=duration",
		"-of", "default=noprint_wrappers=1:nokey=1",
		videoPath,
	)
	out, err := cmd.Output()
	if err != nil {
		return 0, fmt.Errorf("ffprobe: %w", err)
	}
	dur, err := strconv.ParseFloat(strings.TrimSpace(string(out)), 64)
	if err != nil {
		return 0, fmt.Errorf("parse duration %q: %w", out, err)
	}
	return dur, nil
}

// classifyFrames sends each extracted frame to the image classifier and
// reduces the per-frame results to a single worst-case verdict.
//
// "Worst" means: any frame that returns ACTION_BLOCK forces the overall
// verdict to BLOCK; ACTION_WARN promotes WARN; otherwise ALLOW. The reported
// scores are the per-label maxima across all frames.
func (s *Server) classifyFrames(
	ctx context.Context,
	frames []string,
	requestID string,
) ([]*classifyv1.Score, int, classifyv1.Action, string) {
	if len(frames) == 0 {
		return nil, 0, classifyv1.Action_ACTION_BLOCK, "video_sampler:no_frames"
	}

	maxScores := map[string]float32{}
	worstAction := classifyv1.Action_ACTION_ALLOW
	worstReason := ""
	scored := 0
	for _, path := range frames {
		data, err := os.ReadFile(path) //nolint:gosec // path under temp dir we created
		if err != nil {
			slog.Warn("read frame", "err", err, "path", path)
			continue
		}
		t0 := time.Now()
		res, err := s.cfg.Image.Classify(ctx, &classifyv1.ClassifyImageRequest{
			ImageData: data,
			RequestId: requestID,
		})
		frameLatency.Observe(time.Since(t0).Seconds())
		if err != nil {
			slog.Warn("classify frame", "err", err, "path", path)
			continue
		}
		scored++
		for _, sc := range res.Scores {
			if sc.Value > maxScores[sc.Label] {
				maxScores[sc.Label] = sc.Value
			}
		}
		if res.Action == classifyv1.Action_ACTION_BLOCK {
			worstAction = classifyv1.Action_ACTION_BLOCK
			worstReason = "video:" + res.Reason
		} else if res.Action == classifyv1.Action_ACTION_WARN && worstAction != classifyv1.Action_ACTION_BLOCK {
			worstAction = classifyv1.Action_ACTION_WARN
			worstReason = "video:" + res.Reason
		}
	}
	if scored == 0 {
		return nil, 0, classifyv1.Action_ACTION_ALLOW, "video_sampler:classification_failed"
	}

	out := make([]*classifyv1.Score, 0, len(maxScores))
	for k, v := range maxScores {
		out = append(out, &classifyv1.Score{Label: k, Value: v})
	}
	sort.Slice(out, func(i, j int) bool { return out[i].Label < out[j].Label })
	return out, scored, worstAction, worstReason
}
