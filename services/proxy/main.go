// Package main is the entrypoint for the see-no-evil MITM proxy data-plane.
//
// The proxy:
//   - Terminates HTTP CONNECT tunnels and inspects HTTPS traffic using an
//     on-the-fly leaf certificate signed by the see-no-evil CA.
//   - Forwards plain HTTP transparently, injecting SafeSearch parameters and
//     YouTube Restricted-Mode cookies.
//   - For inspectable body types (image/*, text/html, text/plain, video/*) it
//     streams the body to the appropriate classifier gRPC service and then
//     calls the policy API to decide allow / block / warn.
//   - SNI-bypass domains are tunneled as plain CONNECT with no inspection.
//   - Exposes Prometheus metrics on :9100/metrics.

package main

import (
	"context"
	"crypto/tls"
	"flag"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/ca"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/classifier"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/config"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/mitm"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/policy"
)

func main() {
	healthcheck := flag.Bool("healthcheck", false, "hit /healthz and exit")
	flag.Parse()

	if *healthcheck {
		resp, err := http.Get("http://localhost:9100/healthz")
		if err != nil || resp.StatusCode != http.StatusOK {
			os.Exit(1)
		}
		os.Exit(0)
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	}))
	slog.SetDefault(logger)

	cfg, err := config.Load(os.Getenv("CONFIG_PATH"))
	if err != nil {
		slog.Error("failed to load config", "err", err)
		os.Exit(1)
	}

	// Load or generate the MITM CA.
	caKeyPair, err := ca.LoadFromFields(
		cfg.Proxy.CA.Mode,
		cfg.Proxy.CA.CertPath,
		cfg.Proxy.CA.KeyPath,
		cfg.Proxy.CA.DataDir,
	)
	if err != nil {
		slog.Error("failed to load CA", "err", err)
		os.Exit(1)
	}
	slog.Info("CA loaded", "subject", caKeyPair.Cert.Subject.CommonName)

	// gRPC classifier clients.
	classifierClients, err := classifier.NewClientsFromAddrs(
		cfg.Classifiers.Image.Addr,
		cfg.Classifiers.Text.Addr,
		cfg.Classifiers.Video.Addr,
	)
	if err != nil {
		slog.Error("failed to dial classifier services", "err", err)
		os.Exit(1)
	}
	defer classifierClients.Close()

	// Policy API client (HTTP, talks to the api service).
	policyClient := policy.NewClient(cfg.PolicyAPIURL())

	// Build the MITM proxy handler.
	handler := mitm.NewHandler(mitm.Config{
		CA:            caKeyPair,
		BypassDomains: cfg.Proxy.BypassDomains,
		SafeSearch: mitm.SafeSearchCfg{
			Google:            cfg.Proxy.SafeSearch.Google,
			Bing:              cfg.Proxy.SafeSearch.Bing,
			DuckDuckGo:        cfg.Proxy.SafeSearch.DuckDuckGo,
			YouTubeRestricted: cfg.Proxy.SafeSearch.YouTubeRestricted,
		},
		MaxInspectBytes: cfg.Proxy.MaxInspectBytes(),
		Classifiers:     classifierClients,
		Policy:          policyClient,
		TextInspection: mitm.TextInspectionCfg{
			Mode:          cfg.Proxy.TextInspection.Mode,
			NSFWThreshold: cfg.Proxy.TextInspection.NSFWThreshold,
			Redaction:     cfg.Proxy.TextInspection.Redaction,
		},
	})

	// HTTP proxy listener.
	proxySrv := &http.Server{
		Addr:              cfg.Proxy.BindHTTP,
		Handler:           handler,
		ReadHeaderTimeout: 30 * time.Second,
		TLSConfig: &tls.Config{
			MinVersion: tls.VersionTLS12,
		},
	}

	// Metrics server.
	metricsMux := http.NewServeMux()
	metricsMux.Handle("/metrics", promhttp.Handler())
	metricsMux.HandleFunc("/healthz", func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusOK)
	})
	metricsSrv := &http.Server{
		Addr:              cfg.Proxy.MetricsAddr,
		Handler:           metricsMux,
		ReadHeaderTimeout: 5 * time.Second,
	}

	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, syscall.SIGINT)
	defer stop()

	go func() {
		slog.Info("proxy listening", "addr", proxySrv.Addr)
		if err := proxySrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("proxy server error", "err", err)
		}
	}()
	go func() {
		slog.Info("metrics listening", "addr", metricsSrv.Addr)
		if err := metricsSrv.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("metrics server error", "err", err)
		}
	}()

	<-ctx.Done()
	slog.Info("shutting down")

	shutCtx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	_ = proxySrv.Shutdown(shutCtx)
	_ = metricsSrv.Shutdown(shutCtx)
}
