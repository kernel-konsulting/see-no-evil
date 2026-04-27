// Package main implements the see-no-evil video sampler.
//
// It accepts a streamed video upload over gRPC, writes it to a temporary
// file, uses ffmpeg to extract N evenly-spaced frames, hands each frame to
// the image classifier, and returns the worst-case verdict + a thumbnail of
// the most-offensive frame for the quarantine UI.
package main

import (
	"context"
	"flag"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"syscall"

	"github.com/prometheus/client_golang/prometheus/promhttp"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/health"
	healthpb "google.golang.org/grpc/health/grpc_health_v1"

	classifyv1 "github.com/kernel-konsulting/see-no-evil/services/video-sampler/gen/classify/v1"
	"github.com/kernel-konsulting/see-no-evil/services/video-sampler/internal/sampler"
)

func main() {
	healthcheck := flag.Bool("healthcheck", false, "perform health probe and exit")
	flag.Parse()

	if *healthcheck {
		// Lightweight TCP probe — controller-side health is fine for the stub.
		conn, err := net.Dial("tcp", "127.0.0.1:50053")
		if err != nil {
			os.Exit(1)
		}
		_ = conn.Close()
		os.Exit(0)
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	port := envOr("VIDEO_SAMPLER_PORT", "50053")
	imageAddr := envOr("IMAGE_CLASSIFIER_ADDR", "image-classifier:50051")
	maxFrames, _ := strconv.Atoi(envOr("VIDEO_SAMPLER_MAX_FRAMES", "8"))
	maxBytes, _ := strconv.ParseInt(envOr("VIDEO_SAMPLER_MAX_BYTES", "52428800"), 10, 64) // 50 MiB
	metricsAddr := envOr("METRICS_ADDR", "0.0.0.0:9103")

	imgConn, err := grpc.NewClient(imageAddr, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		slog.Error("dial image-classifier", "err", err, "addr", imageAddr)
		os.Exit(1)
	}
	defer imgConn.Close()

	srv := sampler.NewServer(sampler.Config{
		Image:           classifyv1.NewImageClassifierClient(imgConn),
		DefaultFrames:   maxFrames,
		MaxVideoBytes:   maxBytes,
		FFmpegPath:      envOr("FFMPEG_PATH", "ffmpeg"),
		ThumbnailWidth:  256,
		ThumbnailJPEGQ:  60,
	})

	gs := grpc.NewServer()
	classifyv1.RegisterVideoSamplerServer(gs, srv)
	hs := health.NewServer()
	hs.SetServingStatus("seenoevil.classify.v1.VideoSampler", healthpb.HealthCheckResponse_SERVING)
	hs.SetServingStatus("", healthpb.HealthCheckResponse_SERVING)
	healthpb.RegisterHealthServer(gs, hs)

	lis, err := net.Listen("tcp", ":"+port)
	if err != nil {
		slog.Error("listen", "err", err, "port", port)
		os.Exit(1)
	}

	// Metrics
	metricsMux := http.NewServeMux()
	metricsMux.Handle("/metrics", promhttp.Handler())
	metricsMux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	metricsSrv := &http.Server{Addr: metricsAddr, Handler: metricsMux, ReadHeaderTimeout: 5}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	go func() {
		slog.Info("video-sampler listening", "port", port)
		if err := gs.Serve(lis); err != nil {
			slog.Error("grpc serve", "err", err)
		}
	}()
	go func() {
		slog.Info("metrics listening", "addr", metricsAddr)
		if err := metricsSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("metrics serve", "err", err)
		}
	}()

	<-ctx.Done()
	slog.Info("shutting down")
	gs.GracefulStop()
	_ = metricsSrv.Shutdown(context.Background())
}

func envOr(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}
