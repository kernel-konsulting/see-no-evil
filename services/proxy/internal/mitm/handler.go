// Package mitm implements the HTTP/HTTPS MITM proxy handler.
//
// Architecture:
//
//	HTTP CONNECT  → hijack connection → TLS handshake with leaf cert →
//	   treat as plain HTTP → inspect body → call classifiers → call policy API.
//
//	Plain HTTP    → inspect body → call classifiers → call policy API.
//
//	Bypass domain → CONNECT tunnel, no inspection.
package mitm

import (
	"bytes"
	"context"
	"crypto/tls"
	"fmt"
	"html"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"

	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/ca"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/classifier"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/policy"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/safesearch"
)

// ---------------------------------------------------------------------------
// Prometheus metrics
// ---------------------------------------------------------------------------

var (
	requestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "proxy_requests_total",
		Help: "Total HTTP(S) requests processed by the proxy",
	}, []string{"decision"})

	requestDuration = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "proxy_request_duration_seconds",
		Help:    "End-to-end request latency",
		Buckets: []float64{0.05, 0.1, 0.25, 0.5, 1, 2.5, 5},
	}, []string{"decision"})

	classifierErrors = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "proxy_classifier_errors_total",
		Help: "Classifier call failures (classified as allow-on-error)",
	}, []string{"classifier"})
)

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

// Config holds all dependencies for the MITM handler.
type Config struct {
	CA              *ca.KeyPair
	BypassDomains   []string
	SafeSearch      SafeSearchCfg
	MaxInspectBytes int64
	Classifiers     *classifier.Clients
	Policy          *policy.Client
}

// SafeSearchCfg mirrors proxy.safesearch from config.yaml.
type SafeSearchCfg struct {
	Google            bool
	Bing              bool
	DuckDuckGo        bool
	YouTubeRestricted bool
}

// ---------------------------------------------------------------------------
// Handler
// ---------------------------------------------------------------------------

// Handler is an http.Handler that proxies and optionally inspects traffic.
type Handler struct {
	cfg       Config
	leafCache *ca.LeafCache
	bypass    map[string]bool // lower-case bypass domain set
	ssCfg     safesearch.Config
}

func NewHandler(cfg Config) *Handler {
	bypass := make(map[string]bool, len(cfg.BypassDomains))
	for _, d := range cfg.BypassDomains {
		bypass[strings.ToLower(strings.TrimPrefix(d, "*."))] = true
	}
	return &Handler{
		cfg:       cfg,
		leafCache: ca.NewLeafCache(cfg.CA),
		bypass:    bypass,
		ssCfg: safesearch.Config{
			Google:            cfg.SafeSearch.Google,
			Bing:              cfg.SafeSearch.Bing,
			DuckDuckGo:        cfg.SafeSearch.DuckDuckGo,
			YouTubeRestricted: cfg.SafeSearch.YouTubeRestricted,
		},
	}
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.Method == http.MethodConnect {
		h.handleConnect(w, r)
		return
	}
	h.handlePlainHTTP(w, r)
}

// ---------------------------------------------------------------------------
// CONNECT (HTTPS)
// ---------------------------------------------------------------------------

func (h *Handler) handleConnect(w http.ResponseWriter, r *http.Request) {
	host := r.Host

	// Bypass: tunnel without inspection.
	if h.isBypass(host) {
		h.tunnel(w, r, host)
		return
	}

	// Hijack the connection.
	hj, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "hijacking not supported", http.StatusInternalServerError)
		return
	}
	clientConn, _, err := hj.Hijack()
	if err != nil {
		slog.Error("hijack failed", "err", err)
		return
	}
	defer clientConn.Close()

	// Tell the client the tunnel is established.
	_, _ = clientConn.Write([]byte("HTTP/1.1 200 Connection established\r\n\r\n"))

	// Wrap with our TLS using a leaf cert for this host.
	tlsConn := tls.Server(clientConn, h.leafCache.TLSConfig(host))
	if err := tlsConn.Handshake(); err != nil {
		slog.Debug("TLS handshake failed", "host", host, "err", err)
		return
	}
	defer tlsConn.Close()

	// Re-use the plain HTTP handler on the decrypted connection.
	httpSrv := &http.Server{ //nolint:gosec // timeouts set per-request
		Handler: http.HandlerFunc(func(w http.ResponseWriter, innerR *http.Request) {
			innerR.URL.Host = host
			innerR.URL.Scheme = "https"
			innerR.Host = host
			h.handlePlainHTTP(w, innerR)
		}),
	}
	_ = httpSrv.Serve(newSingleConnListener(tlsConn))
}

// ---------------------------------------------------------------------------
// Plain HTTP (and decrypted HTTPS)
// ---------------------------------------------------------------------------

func (h *Handler) handlePlainHTTP(w http.ResponseWriter, r *http.Request) {
	t0 := time.Now()

	// Apply SafeSearch rewrites.
	safesearch.RewriteRequest(r, h.ssCfg)

	// Remove hop-by-hop headers.
	removeHopHeaders(r.Header)
	r.Header.Del("Proxy-Connection")

	// Read the request body for inspection (if applicable).
	var reqBodySnap []byte
	if r.Body != nil && r.ContentLength != 0 {
		reqBodySnap, r.Body = peekBody(r.Body, h.cfg.MaxInspectBytes)
	}

	// Forward the request upstream.
	resp, err := h.roundTrip(r)
	if err != nil {
		http.Error(w, "upstream error", http.StatusBadGateway)
		return
	}
	defer resp.Body.Close()

	// Read response body for inspection.
	var respBodySnap []byte
	if shouldInspect(resp.Header.Get("Content-Type")) {
		respBodySnap, resp.Body = peekBody(resp.Body, h.cfg.MaxInspectBytes)
	}

	// Classify and decide.
	decision := h.decide(r.Context(), r, resp, reqBodySnap, respBodySnap)
	requestsTotal.WithLabelValues(decision).Inc()
	requestDuration.WithLabelValues(decision).Observe(time.Since(t0).Seconds())

	if decision == "block" {
		writeBlockPage(w, r.URL.String())
		return
	}

	// Copy the response to the client.
	copyHeaders(w.Header(), resp.Header)
	w.WriteHeader(resp.StatusCode)
	_, _ = io.Copy(w, resp.Body)
}

// ---------------------------------------------------------------------------
// Decision pipeline
// ---------------------------------------------------------------------------

func (h *Handler) decide(
	ctx context.Context,
	r *http.Request,
	resp *http.Response,
	reqBody, respBody []byte,
) string {
	ct := resp.Header.Get("Content-Type")
	scores := make(map[string]float32)

	// Image classification.
	if isImage(ct) && len(respBody) > 0 && h.cfg.Classifiers != nil {
		result, err := h.cfg.Classifiers.ClassifyImage(ctx, respBody, r.Header.Get("X-Request-Id"))
		if err != nil {
			slog.Warn("image classifier error", "err", err)
			classifierErrors.WithLabelValues("image").Inc()
		} else {
			for k, v := range result.Scores {
				scores["image:"+k] = v
			}
			if result.Action.String() == "ACTION_BLOCK" {
				_ = h.auditDecide(ctx, r, ct, scores, "block")
				return "block"
			}
		}
	}

	// Text classification (response body).
	if isText(ct) && len(respBody) > 0 && h.cfg.Classifiers != nil {
		text := string(respBody)
		if len(text) > 4096 {
			text = text[:4096]
		}
		result, err := h.cfg.Classifiers.ClassifyText(ctx, text, r.Header.Get("X-Request-Id"))
		if err != nil {
			slog.Warn("text classifier error", "err", err)
			classifierErrors.WithLabelValues("text").Inc()
		} else {
			for k, v := range result.Scores {
				scores["text:"+k] = v
			}
			if result.Action.String() == "ACTION_BLOCK" {
				_ = h.auditDecide(ctx, r, ct, scores, "block")
				return "block"
			}
		}
	}

	// Policy API for domain/schedule/quota checks.
	if h.cfg.Policy != nil {
		mac := r.Header.Get("X-Device-Mac")
		result, err := h.cfg.Policy.Decide(ctx, policy.DecideRequest{
			URL:              r.URL.String(),
			DeviceMAC:        mac,
			ContentType:      ct,
			ClassifierScores: scores,
		})
		if err != nil {
			slog.Warn("policy API error", "err", err)
			// Fail-open on policy API errors (classifiers already ran).
			return "allow"
		}
		_ = h.auditDecide(ctx, r, ct, scores, result.Decision)
		return result.Decision
	}

	return "allow"
}

func (h *Handler) auditDecide(ctx context.Context, r *http.Request, ct string, scores map[string]float32, decision string) error {
	// Best-effort — ignore errors.
	_, err := h.cfg.Policy.Decide(ctx, policy.DecideRequest{
		URL:              r.URL.String(),
		DeviceMAC:        r.Header.Get("X-Device-Mac"),
		ContentType:      ct,
		ClassifierScores: scores,
	})
	return err
}

// ---------------------------------------------------------------------------
// Bypass tunnel (no inspection)
// ---------------------------------------------------------------------------

func (h *Handler) tunnel(w http.ResponseWriter, r *http.Request, addr string) {
	upstream, err := net.DialTimeout("tcp", addr, 10*time.Second)
	if err != nil {
		http.Error(w, "tunnel dial failed", http.StatusBadGateway)
		return
	}
	defer upstream.Close()

	hj, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "hijacking not supported", http.StatusInternalServerError)
		return
	}
	clientConn, _, err := hj.Hijack()
	if err != nil {
		return
	}
	defer clientConn.Close()

	_, _ = clientConn.Write([]byte("HTTP/1.1 200 Connection established\r\n\r\n"))

	done := make(chan struct{}, 2)
	cp := func(dst, src net.Conn) {
		_, _ = io.Copy(dst, src)
		done <- struct{}{}
	}
	go cp(upstream, clientConn)
	go cp(clientConn, upstream)
	<-done
}

// ---------------------------------------------------------------------------
// HTTP forwarding helpers
// ---------------------------------------------------------------------------

func (h *Handler) roundTrip(r *http.Request) (*http.Response, error) {
	transport := &http.Transport{
		TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12},
	}
	// Strip Proxy-Authorization so it doesn't leak upstream.
	r.Header.Del("Proxy-Authorization")
	return transport.RoundTrip(r)
}

var hopHeaders = []string{
	"Connection", "Keep-Alive", "Proxy-Authenticate", "Proxy-Authorization",
	"Te", "Trailers", "Transfer-Encoding", "Upgrade",
}

func removeHopHeaders(h http.Header) {
	for _, k := range hopHeaders {
		h.Del(k)
	}
}

func copyHeaders(dst, src http.Header) {
	for k, vv := range src {
		for _, v := range vv {
			dst.Add(k, v)
		}
	}
}

// ---------------------------------------------------------------------------
// Body peeking
// ---------------------------------------------------------------------------

// peekBody reads up to limit bytes from rc and returns the snapshot plus a
// new io.ReadCloser that replays the full body (snapshot + remainder).
func peekBody(rc io.ReadCloser, limit int64) ([]byte, io.ReadCloser) {
	snap, err := io.ReadAll(io.LimitReader(rc, limit))
	if err != nil {
		return nil, rc
	}
	return snap, io.NopCloser(io.MultiReader(bytes.NewReader(snap), rc))
}

// ---------------------------------------------------------------------------
// Content-type predicates
// ---------------------------------------------------------------------------

func shouldInspect(ct string) bool {
	return isImage(ct) || isText(ct)
}

func isImage(ct string) bool {
	return strings.HasPrefix(ct, "image/")
}

func isText(ct string) bool {
	return strings.HasPrefix(ct, "text/") || ct == "application/json"
}

// ---------------------------------------------------------------------------
// Bypass domain matching
// ---------------------------------------------------------------------------

func (h *Handler) isBypass(host string) bool {
	// Strip port.
	bare, _, err := net.SplitHostPort(host)
	if err != nil {
		bare = host
	}
	bare = strings.ToLower(bare)

	// Exact match.
	if h.bypass[bare] {
		return true
	}
	// Wildcard: check each suffix level.
	parts := strings.SplitN(bare, ".", 2)
	if len(parts) == 2 && h.bypass[parts[1]] {
		return true
	}
	return false
}

// ---------------------------------------------------------------------------
// Block page
// ---------------------------------------------------------------------------

func writeBlockPage(w http.ResponseWriter, rawURL string) {
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	w.WriteHeader(http.StatusForbidden)
	fmt.Fprintf(w, `<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Blocked — see-no-evil</title></head>
<body>
<h1>This page has been blocked</h1>
<p>URL: <code>%s</code></p>
<p>If you think this is a mistake, contact your administrator.</p>
</body>
</html>`, html.EscapeString(rawURL))
}

// ---------------------------------------------------------------------------
// Single-conn listener (for serving one HTTP request over a hijacked conn)
// ---------------------------------------------------------------------------

type singleConnListener struct {
	conn net.Conn
	ch   chan net.Conn
}

func newSingleConnListener(conn net.Conn) net.Listener {
	ch := make(chan net.Conn, 1)
	ch <- conn
	return &singleConnListener{conn: conn, ch: ch}
}

func (l *singleConnListener) Accept() (net.Conn, error) {
	conn, ok := <-l.ch
	if !ok {
		return nil, fmt.Errorf("listener closed")
	}
	return conn, nil
}

func (l *singleConnListener) Close() error {
	close(l.ch)
	return nil
}

func (l *singleConnListener) Addr() net.Addr { return l.conn.LocalAddr() }
