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
	"encoding/binary"
	"encoding/xml"
	"fmt"
	"html"
	"image"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"

	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/ca"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/classifier"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/policy"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/preview"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/quota"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/runtime"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/safesearch"
	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/textextract"
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

	peekBodyErrors = promauto.NewCounter(prometheus.CounterOpts{
		Name: "proxy_peek_body_errors_total",
		Help: "Response body read errors during inspection (truncated chunked etc.)",
	})
)

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------

// Config holds all dependencies for the MITM handler.
type Config struct {
	CA            *ca.KeyPair
	BypassDomains []string
	SafeSearch    SafeSearchCfg
	// MaxInspectBytes is the legacy global cap; per-type caps below take
	// precedence. Kept for backwards compatibility.
	MaxInspectBytes int64
	// Per-content-type caps. 0 = use MaxInspectBytes; very large (1<<62) = no cap.
	MaxImageBytes  int64
	MaxTextBytes   int64
	MaxVideoBytes  int64
	Classifiers    *classifier.Clients
	Runtime        *runtime.Poller
	Policy         *policy.Client
	Quota          *quota.Reporter
	TextInspection TextInspectionCfg
	// FailClosed blocks when classifiers or the policy API fail, instead of
	// failing open. Default false (degraded filtering beats a broken network).
	FailClosed bool
}

// TextInspectionCfg controls how the text classifier verdict is applied.
type TextInspectionCfg struct {
	Mode          string // "off" | "block" | "strip" (default "block")
	NSFWThreshold float32
	Redaction     string
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
	cfg                  Config
	leafCache            *ca.LeafCache
	bypass               map[string]bool // lower-case bypass domain set
	ssCfg                safesearch.Config
	blockedYouTubeMu     sync.Mutex
	blockedYouTubeVideos map[string]blockedYouTubeVideo
	transport            *http.Transport // shared upstream transport (conn reuse)
	limiter              *rateLimiter
}

type blockedYouTubeVideo struct {
	reason    string
	expiresAt time.Time
}

const blockedYouTubeTTL = 6 * time.Hour
const maxBlockedYouTube = 10000

// hard caps mirror config hard caps — defense in depth even if config is bypassed.
const (
	hardMaxImage = 20 << 20
	hardMaxText  = 5 << 20
	hardMaxVideo = 500 << 20
)

// ---------------------------------------------------------------------------
// Rate limiter (data-plane, per-IP token bucket stub)
// ---------------------------------------------------------------------------

type rateLimiter struct {
	mu        sync.Mutex
	buckets   map[string]*rateBucket
	limit     int
	window    time.Duration
	lastSweep time.Time
}

type rateBucket struct {
	count int
	reset time.Time
}

func newRateLimiter(limit int, window time.Duration) *rateLimiter {
	return &rateLimiter{
		buckets: make(map[string]*rateBucket),
		limit:   limit,
		window:  window,
	}
}

func (rl *rateLimiter) allow(ip string) bool {
	if ip == "" {
		return true
	}
	now := time.Now()
	rl.mu.Lock()
	defer rl.mu.Unlock()
	// periodic sweep of expired buckets to bound memory (every ~5m)
	if now.Sub(rl.lastSweep) > 5*time.Minute {
		for k, b := range rl.buckets {
			if now.After(b.reset) {
				delete(rl.buckets, k)
			}
		}
		rl.lastSweep = now
	}
	b, ok := rl.buckets[ip]
	if !ok || now.After(b.reset) {
		rl.buckets[ip] = &rateBucket{count: 1, reset: now.Add(rl.window)}
		return true
	}
	if b.count >= rl.limit {
		return false
	}
	b.count++
	return true
}

func NewHandler(cfg Config) *Handler {
	bypass := make(map[string]bool, len(cfg.BypassDomains))
	for _, d := range cfg.BypassDomains {
		bypass[strings.ToLower(strings.TrimPrefix(d, "*."))] = true
	}
	h := &Handler{
		cfg:                  cfg,
		leafCache:            ca.NewLeafCache(cfg.CA),
		bypass:               bypass,
		blockedYouTubeVideos: make(map[string]blockedYouTubeVideo),
		limiter:              newRateLimiter(100, time.Second),
		transport: &http.Transport{
			Proxy: http.ProxyFromEnvironment,
			DialContext: (&net.Dialer{
				Timeout:   10 * time.Second,
				KeepAlive: 30 * time.Second,
			}).DialContext,
			TLSClientConfig:       &tls.Config{MinVersion: tls.VersionTLS12},
			TLSHandshakeTimeout:   10 * time.Second,
			ResponseHeaderTimeout: 60 * time.Second,
			IdleConnTimeout:       90 * time.Second,
			MaxIdleConns:          100,
			MaxIdleConnsPerHost:   16,
		},
		ssCfg: safesearch.Config{
			Google:            cfg.SafeSearch.Google,
			Bing:              cfg.SafeSearch.Bing,
			DuckDuckGo:        cfg.SafeSearch.DuckDuckGo,
			YouTubeRestricted: cfg.SafeSearch.YouTubeRestricted,
		},
	}
	go h.sweepBlockedYouTubeLoop()
	return h
}

func (h *Handler) sweepBlockedYouTubeLoop() {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()
	for range ticker.C {
		h.cleanupBlockedYouTube()
	}
}

func (h *Handler) cleanupBlockedYouTube() {
	now := time.Now()
	h.blockedYouTubeMu.Lock()
	defer h.blockedYouTubeMu.Unlock()
	for id, v := range h.blockedYouTubeVideos {
		if now.After(v.expiresAt) {
			delete(h.blockedYouTubeVideos, id)
		}
	}
	// hard bound: if still over capacity evict arbitrary oldest entries
	for len(h.blockedYouTubeVideos) > maxBlockedYouTube {
		for k := range h.blockedYouTubeVideos {
			delete(h.blockedYouTubeVideos, k)
			break
		}
	}
}

func (h *Handler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	ip := clientIP(r)
	if h.limiter != nil && !h.limiter.allow(ip) {
		http.Error(w, "Too Many Requests", http.StatusTooManyRequests)
		return
	}
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
	defer func() { _ = clientConn.Close() }()

	// Tell the client the tunnel is established.
	_, _ = clientConn.Write([]byte("HTTP/1.1 200 Connection established\r\n\r\n"))

	// Wrap with our TLS using a leaf cert for this host.
	tlsConn := tls.Server(clientConn, h.leafCache.TLSConfig(host))
	_ = clientConn.SetDeadline(time.Now().Add(10 * time.Second))
	if err := tlsConn.Handshake(); err != nil {
		slog.Debug("TLS handshake failed", "host", host, "err", err)
		return
	}
	_ = clientConn.SetDeadline(time.Time{})
	defer func() { _ = tlsConn.Close() }()

	// Re-use the plain HTTP handler on the decrypted connection.
	httpSrv := &http.Server{ //nolint:gosec // timeouts set below
		Handler: http.HandlerFunc(func(w http.ResponseWriter, innerR *http.Request) {
			innerR.URL.Host = host
			innerR.URL.Scheme = "https"
			innerR.Host = host
			h.handlePlainHTTP(w, innerR)
		}),
		ReadHeaderTimeout: 30 * time.Second,
		ReadTimeout:       60 * time.Second,
		WriteTimeout:      60 * time.Second,
		IdleTimeout:       2 * time.Minute,
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

	// Device identity is derived from the source IP only. Client-supplied
	// identity headers are untrusted: a filtered device could otherwise
	// claim a different MAC (e.g. a parent's) to inherit its profile, and
	// the header would leak upstream.
	r.Header.Del("X-Device-Mac")

	ip := clientIP(r)
	if h.cfg.Quota != nil {
		h.cfg.Quota.NoteActivity(ip)
	}

	// Read the request body for inspection (if applicable). Request bodies
	// (POSTed forms / uploads) use the text limit since they're typically
	// form data or JSON.
	var reqBodySnap []byte
	if r.Body != nil && r.ContentLength != 0 {
		reqBodySnap, r.Body = peekBody(r.Body, h.limitFor(r.Header.Get("Content-Type")))
	}

	if reason, ok := h.blockReasonForYouTubeRequest(r, reqBodySnap); ok {
		requestsTotal.WithLabelValues("block").Inc()
		requestDuration.WithLabelValues("block").Observe(time.Since(t0).Seconds())
		slog.Info("request blocked",
			"url", r.URL.String(),
			"method", r.Method,
			"host", r.Host,
			"content_type", r.Header.Get("Accept"),
			"client_ip", ip,
			"reason", reason,
		)
		writeBlockPage(w, r.URL.String(), reason)
		return
	}

	// Forward the request upstream.
	resp, err := h.roundTrip(r)
	if err != nil {
		http.Error(w, "upstream error", http.StatusBadGateway)
		return
	}
	resp = h.refetchFullVideoIfNeeded(r, resp)
	defer func() { _ = resp.Body.Close() }()

	// Read response body for inspection. Use the per-type cap so images and
	// text are sent to the classifier in full — truncating an image past
	// its header makes the decoder reject it.
	var respBodySnap []byte
	respCT := resp.Header.Get("Content-Type")
	if shouldInspect(respCT) {
		respBodySnap, resp.Body = peekBody(resp.Body, h.limitFor(respCT))
	} else {
		// Content-Type spoofing defence: sniff generic types with bounded buffer
		// to catch NSFW images served as application/octet-stream etc. (#3).
		sniffSnap, newBody := peekBody(resp.Body, hardMaxImage)
		if shouldInspectWithBody(respCT, sniffSnap) {
			respBodySnap = sniffSnap
			resp.Body = newBody
		} else {
			// Not inspectable even after sniff — keep body but discard snap to
			// avoid holding large generic bodies in memory for audit.
			resp.Body = newBody
		}
	}

	// Classify and decide. May return a replacement body when text-strip mode
	// is enabled and the classifier flagged some segments.
	decision, reason, replacementBody := h.decide(r.Context(), r, resp, reqBodySnap, respBodySnap)
	requestsTotal.WithLabelValues(decision).Inc()
	requestDuration.WithLabelValues(decision).Observe(time.Since(t0).Seconds())

	if decision == "block" {
		reason = nonEmptyReason(reason, "unspecified_block")
		slog.Info("request blocked",
			"url", r.URL.String(),
			"method", r.Method,
			"host", r.Host,
			"content_type", respCT,
			"client_ip", ip,
			"reason", reason,
		)
		writeBlockedResponse(w, r.URL.String(), reason, respCT, respBodySnap)
		return
	}

	// Copy the response to the client.
	copyHeaders(w.Header(), resp.Header)
	if shouldInspect(respCT) {
		markNoStore(w.Header())
	}
	if replacementBody != nil {
		// We rewrote the body — original Content-Length is now stale.
		w.Header().Set("Content-Length", fmt.Sprintf("%d", len(replacementBody)))
		w.WriteHeader(resp.StatusCode)
		_, _ = w.Write(replacementBody)
		return
	}
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
) (string, string, []byte) {
	ct := resp.Header.Get("Content-Type")
	scores := make(map[string]float32)
	var replacementBody []byte
	// thumb is the un-blurred small JPEG of the analysed image, attached to
	// every audit row for image responses so admins can verify scoring.
	var thumb string

	// Live runtime settings (image/text/video toggles).
	rt := runtime.Settings{}
	if h.cfg.Runtime != nil {
		if cur := h.cfg.Runtime.Get(); cur != nil {
			rt = *cur
		}
	} else {
		rt.Inspect.Image = true
		rt.Inspect.Video = true
		rt.Inspect.Text = true
		rt.Inspect.Domain = true
		rt.Inspect.URL = true
	}
	if !rt.Inspect.Domain && !rt.Inspect.URL {
		// Keep the zero-value runtime struct from accidentally disabling both
		// domain and URL checks before the first runtime poll succeeds.
		rt.Inspect.Domain = true
		rt.Inspect.URL = true
	}

	// Image classification (includes sniff fallback for spoofed CT).
	if rt.Inspect.Image && effectiveIsImage(ct, respBody) && len(respBody) > 0 && h.cfg.Classifiers != nil {
		ictx, cancel := context.WithTimeout(ctx, 5*time.Second)
		result, err := h.cfg.Classifiers.ClassifyImage(ictx, respBody, r.Header.Get("X-Request-Id"))
		cancel()
		if err != nil {
			slog.Warn("image classifier error", "err", err)
			classifierErrors.WithLabelValues("image").Inc()
			if h.cfg.FailClosed {
				reason := "classifier:image:unavailable"
				_ = h.auditDecide(ctx, r, ct, scores, "block", reason, "")
				return "block", reason, nil
			}
		} else {
			for k, v := range result.Scores {
				scores["image:"+k] = v
			}
			// Always capture a clear thumbnail; cheap and lets the audit log
			// show what was actually scored.
			thumb, _ = preview.Clear(respBody)
			// INFO log so admins can see what each image scored — invaluable
			// when tuning thresholds.
			slog.Info("image classified",
				"url", r.URL.String(),
				"action", result.Action.String(),
				"scores", result.Scores,
				"bytes", len(respBody),
			)
			if result.Action.String() == "ACTION_BLOCK" {
				reason := classifierBlockReason("image", result.Reason, result.Scores)
				h.rememberBlockedYouTubeVideo(r, nil, reason)
				_ = h.auditDecide(ctx, r, ct, scores, "block", reason, thumb)
				return "block", reason, nil
			}
		}
	}

	// Video sampling: stream the buffered video body to the video-sampler
	// service which extracts evenly-spaced frames and classifies each.
	if rt.Inspect.Video && isVideo(ct) && len(respBody) > 0 && h.cfg.Classifiers != nil && h.cfg.Classifiers.Video != nil {
		vctx, vcancel := context.WithTimeout(ctx, 30*time.Second)
		result, err := h.cfg.Classifiers.SampleVideo(vctx, respBody, 0, r.Header.Get("X-Request-Id"))
		vcancel()
		if err != nil {
			slog.Warn("video sampler error", "err", err)
			classifierErrors.WithLabelValues("video").Inc()
			if h.cfg.FailClosed {
				reason := "classifier:video:sampler_error"
				_ = h.auditDecide(ctx, r, ct, scores, "block", reason, "")
				return "block", reason, nil
			}
			// Fail-open: a sampler outage must not break video streaming.
		} else if result == nil {
			if h.cfg.FailClosed {
				reason := "classifier:video:sampler_unavailable"
				_ = h.auditDecide(ctx, r, ct, scores, "block", reason, "")
				return "block", reason, nil
			}
		} else if result != nil {
			for k, v := range result.Scores {
				scores["video:"+k] = v
			}
			slog.Info("video sampled",
				"url", r.URL.String(),
				"action", result.Action.String(),
				"reason", result.Reason,
				"frames", result.FramesScored,
				"scores", result.Scores,
				"bytes", len(respBody),
			)
			if isVideoSamplerFailure(result.Reason) {
				if h.cfg.FailClosed {
					reason := classifierBlockReason("video", result.Reason, result.Scores)
					_ = h.auditDecide(ctx, r, ct, scores, "block", reason, "")
					return "block", reason, nil
				}
				// Fail-open: un-inspectable video (DRM, exotic codec, empty
				// stream) is allowed through rather than breaking streaming.
			}
			if result.Action.String() == "ACTION_BLOCK" {
				// We don't have a frame thumbnail returned by the sampler today;
				// the quarantine UI will show the placeholder. (Returning a frame
				// in the proto is tracked for a future iteration.)
				reason := classifierBlockReason("video", result.Reason, result.Scores)
				_ = h.auditDecide(ctx, r, ct, scores, "block", reason, "")
				return "block", reason, nil
			}
		}
	}

	// Text classification (response body) using extracted natural-language
	// segments only — feeding raw HTML/JSON to the classifier produces
	// constant false positives on markup tokens. Falls back to sniff for generic CT.
	if h.textMode() != "off" && rt.Inspect.Text && !skipTextClassification(r) && effectiveIsText(ct, respBody) && len(respBody) > 0 && h.cfg.Classifiers != nil {
		action, reason, segScores, replaced := h.inspectText(ctx, r, ct, respBody, rt.Text.NSFWThreshold, h.cfg.FailClosed)
		for k, v := range segScores {
			scores["text:"+k] = v
		}
		if action == "block" {
			reason = nonEmptyReason(reason, "classifier:text")
			_ = h.auditDecide(ctx, r, ct, scores, "block", reason, thumb)
			return "block", reason, nil
		}
		replacementBody = replaced
	}

	// Policy API for domain/schedule/quota checks.
	if h.cfg.Policy != nil {
		pctx, pcancel := context.WithTimeout(ctx, 5*time.Second)
		result, err := h.cfg.Policy.Decide(pctx, policy.DecideRequest{
			URL:              r.URL.String(),
			ClientIP:         clientIP(r),
			ContentType:      ct,
			ClassifierScores: scores,
			ThumbnailB64:     thumb,
		})
		pcancel()
		if err != nil {
			slog.Warn("policy API error", "err", err)
			if h.cfg.FailClosed {
				return "block", "policy:unavailable", replacementBody
			}
			// Fail-open on policy API errors (classifiers already ran).
			return "allow", "policy_api_error", replacementBody
		}
		if result.Decision == "block" {
			reason := nonEmptyReason(result.Reason, "policy:block")
			return "block", reason, nil
		}
		return result.Decision, result.Reason, replacementBody
	}

	return "allow", "default", replacementBody
}

func skipTextClassification(r *http.Request) bool {
	host := bareHost(r)
	if host == "" && r.URL != nil {
		host = r.URL.Hostname()
	}
	host = strings.ToLower(host)
	path := "/"
	if r.URL != nil && r.URL.Path != "" {
		path = r.URL.Path
	}
	if isGoogleSearchHost(host) && (path == "/" || path == "/search") {
		return true
	}
	return false
}

func isGoogleSearchHost(host string) bool {
	h := strings.TrimPrefix(strings.ToLower(host), "www.")
	return h == "google.com" || strings.HasSuffix(h, ".google.com")
}

// textMode returns the configured text-inspection mode, defaulting to "block".
func (h *Handler) textMode() string {
	m := strings.ToLower(strings.TrimSpace(h.cfg.TextInspection.Mode))
	switch m {
	case "off", "strip", "block":
		return m
	default:
		return "block"
	}
}

func (h *Handler) textThreshold(runtimeThreshold float32) float32 {
	if runtimeThreshold > 0 {
		return runtimeThreshold
	}
	if h.cfg.TextInspection.NSFWThreshold > 0 {
		return h.cfg.TextInspection.NSFWThreshold
	}
	return 0.5
}

func nonEmptyReason(reason, fallback string) string {
	reason = strings.TrimSpace(reason)
	if reason == "" {
		reason = fallback
	}
	if len(reason) > 128 {
		return reason[:128]
	}
	return reason
}

func classifierBlockReason(kind, classifierReason string, scores map[string]float32) string {
	r := strings.TrimSpace(classifierReason)
	r = strings.TrimPrefix(r, "classifier:")
	r = strings.TrimPrefix(r, "video_sampler:")
	for _, prefix := range []string{kind + ":", "image:", "video:", "text:"} {
		r = strings.TrimPrefix(r, prefix)
	}
	if r == "" {
		r = topScoreLabel(scores)
	}
	if r == "" {
		return nonEmptyReason("", "classifier:"+kind)
	}
	return nonEmptyReason("classifier:"+kind+":"+r, "classifier:"+kind)
}

func isVideoSamplerFailure(reason string) bool {
	reason = strings.ToLower(strings.TrimSpace(reason))
	return strings.HasPrefix(reason, "video_sampler:") && strings.Contains(reason, "failed")
}

func topScoreLabel(scores map[string]float32) string {
	var label string
	var best float32
	for k, v := range scores {
		if label == "" || v > best {
			label = k
			best = v
		}
	}
	return label
}

// inspectText runs the text classifier over the natural-language segments of
// body. Returns:
//   - action: "block" (mode=block and a flagged segment exists),
//     "allow" otherwise.
//   - aggregated max scores per label.
//   - replacement body when running in strip mode and at least one segment
//     was redacted.
func (h *Handler) inspectText(
	ctx context.Context,
	r *http.Request,
	contentType string,
	body []byte,
	runtimeThreshold float32,
	failClosed bool,
) (string, string, map[string]float32, []byte) {
	segments := textextract.Extract(contentType, body)
	if len(segments) == 0 {
		return "allow", "", nil, nil
	}
	// Cap sequential RPCs: truncate to first 16 segments to bound latency
	// and protect classifier capacity.
	if len(segments) > 16 {
		segments = segments[:16]
	}

	threshold := h.textThreshold(runtimeThreshold)
	maxScores := map[string]float32{}
	flagged := map[string]bool{} // segment text → true when over threshold
	blockReason := ""
	requestID := r.Header.Get("X-Request-Id")

	for _, seg := range segments {
		tctx, cancel := context.WithTimeout(ctx, 3*time.Second)
		res, err := h.cfg.Classifiers.ClassifyText(tctx, seg.Text, requestID)
		cancel()
		if err != nil {
			slog.Warn("text classifier error", "err", err, "path", seg.Path)
			classifierErrors.WithLabelValues("text").Inc()
			if failClosed {
				return "block", "classifier:text:unavailable", maxScores, nil
			}
			continue
		}
		for k, v := range res.Scores {
			if v > maxScores[k] {
				maxScores[k] = v
			}
		}
		if res.Action.String() == "ACTION_BLOCK" || res.Scores["nsfw"] >= threshold {
			flagged[seg.Text] = true
			if blockReason == "" {
				blockReason = classifierBlockReason("text", res.Reason, res.Scores)
			}
		}
	}

	if len(flagged) == 0 {
		return "allow", "", maxScores, nil
	}

	if h.textMode() == "strip" {
		replacement := h.cfg.TextInspection.Redaction
		if replacement == "" {
			replacement = "[content removed by see-no-evil]"
		}
		newBody, changed := textextract.Strip(contentType, body, func(s string) bool {
			return flagged[s]
		}, replacement)
		if changed {
			return "allow", blockReason, maxScores, newBody
		}
		// Fall through to allow even if rewriting failed; we already logged.
		return "allow", blockReason, maxScores, nil
	}

	// mode == "block"
	return "block", blockReason, maxScores, nil
}

func (h *Handler) auditDecide(ctx context.Context, r *http.Request, ct string, scores map[string]float32, decision string, reason string, thumbnail string) error {
	// Best-effort — ignore errors.
	_, err := h.cfg.Policy.Decide(ctx, policy.DecideRequest{
		URL:              r.URL.String(),
		ClientIP:         clientIP(r),
		ContentType:      ct,
		ClassifierScores: scores,
		Decision:         decision,
		Reason:           reason,
		ThumbnailB64:     thumbnail,
	})
	return err
}

func (h *Handler) rememberBlockedYouTubeVideo(r *http.Request, body []byte, reason string) {
	id := extractYouTubeVideoID(r, body)
	if id == "" || !isYouTubeThumbnailHost(bareHost(r)) {
		return
	}

	h.blockedYouTubeMu.Lock()
	if len(h.blockedYouTubeVideos) >= maxBlockedYouTube {
		for k := range h.blockedYouTubeVideos {
			delete(h.blockedYouTubeVideos, k)
			break
		}
	}
	h.blockedYouTubeVideos[id] = blockedYouTubeVideo{
		reason:    nonEmptyReason(reason, "classifier:image"),
		expiresAt: time.Now().Add(blockedYouTubeTTL),
	}
	h.blockedYouTubeMu.Unlock()

	slog.Info("youtube video marked blocked from thumbnail", "video_id", id, "url", r.URL.String(), "reason", reason)
}

func (h *Handler) blockReasonForYouTubeRequest(r *http.Request, body []byte) (string, bool) {
	if isYouTubeThumbnailHost(bareHost(r)) {
		return "", false
	}
	id := extractYouTubeVideoID(r, body)
	if id == "" {
		return "", false
	}

	now := time.Now()
	h.blockedYouTubeMu.Lock()
	entry, ok := h.blockedYouTubeVideos[id]
	if ok && now.After(entry.expiresAt) {
		delete(h.blockedYouTubeVideos, id)
		ok = false
	}
	h.blockedYouTubeMu.Unlock()
	if !ok {
		return "", false
	}
	return nonEmptyReason("youtube_video_blocked_by_thumbnail:"+entry.reason, "youtube_video_blocked_by_thumbnail"), true
}

func extractYouTubeVideoID(r *http.Request, body []byte) string {
	host := bareHost(r)
	path := strings.Trim(r.URL.Path, "/")

	if isYouTubeThumbnailHost(host) {
		parts := strings.Split(path, "/")
		for i, part := range parts {
			switch part {
			case "vi", "vi_webp", "an_webp":
				if i+1 < len(parts) {
					return cleanYouTubeVideoID(parts[i+1])
				}
			}
		}
		return ""
	}

	if host == "youtu.be" && path != "" {
		return cleanYouTubeVideoID(strings.Split(path, "/")[0])
	}

	if !isYouTubeHost(host) {
		return ""
	}

	if r.URL.Query().Get("v") != "" {
		return cleanYouTubeVideoID(r.URL.Query().Get("v"))
	}

	parts := strings.Split(path, "/")
	if len(parts) >= 2 {
		switch parts[0] {
		case "shorts", "embed", "live":
			return cleanYouTubeVideoID(parts[1])
		}
	}

	return extractYouTubeVideoIDFromBody(body)
}

func extractYouTubeVideoIDFromBody(body []byte) string {
	text := string(body)
	for {
		idx := strings.Index(text, `"videoId"`)
		if idx < 0 {
			return ""
		}
		text = text[idx+len(`"videoId"`):]
		colon := strings.Index(text, ":")
		if colon < 0 {
			return ""
		}
		text = strings.TrimLeft(text[colon+1:], " \t\r\n")
		if len(text) == 0 || text[0] != '"' {
			continue
		}
		text = text[1:]
		end := strings.IndexByte(text, '"')
		if end < 0 {
			return ""
		}
		if id := cleanYouTubeVideoID(text[:end]); id != "" {
			return id
		}
		text = text[end+1:]
	}
}

func cleanYouTubeVideoID(raw string) string {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return ""
	}
	if cut := strings.IndexAny(raw, `?&#/"' `); cut >= 0 {
		raw = raw[:cut]
	}
	if len(raw) < 6 || len(raw) > 32 {
		return ""
	}
	for _, ch := range raw {
		if (ch >= 'a' && ch <= 'z') || (ch >= 'A' && ch <= 'Z') || (ch >= '0' && ch <= '9') || ch == '_' || ch == '-' {
			continue
		}
		return ""
	}
	return raw
}

func bareHost(r *http.Request) string {
	host := r.Host
	if r.URL.Host != "" {
		host = r.URL.Host
	}
	bare, _, err := net.SplitHostPort(host)
	if err == nil {
		host = bare
	}
	return strings.ToLower(strings.TrimSuffix(host, "."))
}

// clientIP returns the source IP of the request from the TCP peer.
//
// Deliberately ignores X-Forwarded-For and similar client-supplied headers:
// trusting them would let a filtered device spoof its identity and inherit
// another device's (e.g. a parent's) profile.
func clientIP(r *http.Request) string {
	if r.RemoteAddr == "" {
		return ""
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func isYouTubeHost(host string) bool {
	return host == "youtube.com" || host == "www.youtube.com" || host == "m.youtube.com" || host == "music.youtube.com" || host == "youtube-nocookie.com" || strings.HasSuffix(host, ".youtube.com") || strings.HasSuffix(host, ".youtube-nocookie.com")
}

func isYouTubeThumbnailHost(host string) bool {
	return host == "ytimg.com" || strings.HasSuffix(host, ".ytimg.com") || host == "img.youtube.com"
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
	defer func() { _ = upstream.Close() }()

	hj, ok := w.(http.Hijacker)
	if !ok {
		http.Error(w, "hijacking not supported", http.StatusInternalServerError)
		return
	}
	clientConn, _, err := hj.Hijack()
	if err != nil {
		return
	}
	defer func() { _ = clientConn.Close() }()

	_, _ = clientConn.Write([]byte("HTTP/1.1 200 Connection established\r\n\r\n"))

	done := make(chan struct{}, 2)
	cp := func(dst, src net.Conn) {
		_, _ = io.Copy(dst, src)
		if cw, ok := dst.(interface{ CloseWrite() error }); ok {
			_ = cw.CloseWrite()
		}
		done <- struct{}{}
	}
	go cp(upstream, clientConn)
	go cp(clientConn, upstream)
	for i := 0; i < 2; i++ {
		<-done
	}
}

// ---------------------------------------------------------------------------
// HTTP forwarding helpers
// ---------------------------------------------------------------------------

func (h *Handler) roundTrip(r *http.Request) (*http.Response, error) {
	// Strip Proxy-Authorization so it doesn't leak upstream.
	r.Header.Del("Proxy-Authorization")
	// Let Go's transport negotiate/decode gzip itself. Forwarding the browser's
	// br/gzip header gives us compressed HTML/JSON bytes, which breaks text
	// extraction and gRPC string marshaling.
	r.Header.Del("Accept-Encoding")
	return h.transport.RoundTrip(r)
}

func (h *Handler) refetchFullMediaIfNeeded(r *http.Request, resp *http.Response) *http.Response {
	if r.Method != http.MethodGet || r.Header.Get("Range") == "" {
		return resp
	}
	ct := resp.Header.Get("Content-Type")
	if resp.StatusCode != http.StatusPartialContent || (!isVideo(ct) && !isImage(ct)) {
		return resp
	}
	// Guard: don't refetch if Content-Range indicates huge file beyond hard caps
	if cr := resp.Header.Get("Content-Range"); cr != "" {
		// e.g. "bytes 0-1023/500000000" -> check size
		if idx := strings.LastIndex(cr, "/"); idx >= 0 {
			if sizeStr := strings.TrimSpace(cr[idx+1:]); sizeStr != "*" {
				if sz, err := parsePositiveInt(sizeStr); err == nil && sz > 500<<20 {
					slog.Info("media range refetch skipped — file too large", "url", r.URL.String(), "size", sz)
					return resp
				}
			}
		}
	}

	retry := r.Clone(r.Context())
	retry.Header = r.Header.Clone()
	retry.Header.Del("Range")
	retry.Header.Del("If-Range")
	retry.Header.Del("If-None-Match")
	retry.Header.Del("If-Modified-Since")

	// Add timeout to avoid hanging refetch
	rctx, cancel := context.WithTimeout(r.Context(), 15*time.Second)
	defer cancel()
	retry = retry.WithContext(rctx)

	fullResp, err := h.roundTrip(retry)
	if err != nil {
		slog.Warn("media full-body refetch failed", "err", err, "url", r.URL.String())
		return resp
	}
	fullCT := fullResp.Header.Get("Content-Type")
	if !isVideo(fullCT) && !isImage(fullCT) {
		slog.Warn("media full-body refetch returned non-media", "url", r.URL.String(), "content_type", fullCT)
		_ = fullResp.Body.Close()
		return resp
	}

	_ = resp.Body.Close()
	slog.Info("media range refetched for inspection", "url", r.URL.String(), "status", fullResp.StatusCode, "content_type", fullCT)
	return fullResp
}

func (h *Handler) refetchFullVideoIfNeeded(r *http.Request, resp *http.Response) *http.Response {
	return h.refetchFullMediaIfNeeded(r, resp)
}

func parsePositiveInt(s string) (int64, error) {
	var n int64
	for _, c := range s {
		if c < '0' || c > '9' {
			return 0, fmt.Errorf("not numeric")
		}
		n = n*10 + int64(c-'0')
		if n > 1<<60 {
			return n, nil
		}
	}
	return n, nil
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
	// Do not advertise alternate HTTP/3 routes to clients using the MITM proxy.
	dst.Del("Alt-Svc")
	dst.Del("Alt-Used")
}

func markNoStore(h http.Header) {
	h.Set("Cache-Control", "no-store, private")
	h.Set("Pragma", "no-cache")
	h.Set("Expires", "0")
	h.Del("ETag")
	h.Del("Last-Modified")
}

// ---------------------------------------------------------------------------
// Body peeking
// ---------------------------------------------------------------------------

func capped(v, max int64) int64 {
	if v > max {
		return max
	}
	return v
}

func hasImageMagic(body []byte) bool {
	if len(body) >= 2 && body[0] == 0xFF && body[1] == 0xD8 {
		return true
	}
	if len(body) >= 8 && body[0] == 0x89 && string(body[1:4]) == "PNG" {
		return true
	}
	if len(body) >= 3 && string(body[0:3]) == "GIF" {
		return true
	}
	if len(body) >= 4 && string(body[0:4]) == "RIFF" {
		return true
	}
	if len(body) >= 2 && body[0] == 0x42 && body[1] == 0x4D {
		return true
	}
	return false
}

// peekBody reads up to limit bytes from rc and returns the snapshot plus a
// new io.ReadCloser that replays the full body (snapshot + remainder).
//
// limit==0 falls back to 10 MiB; for an explicit "send everything" pass a
// very large value (e.g. math.MaxInt64). io.ReadAll grows its buffer
// dynamically, so a huge limit doesn't pre-allocate.
func peekBody(rc io.ReadCloser, limit int64) ([]byte, io.ReadCloser) {
	if limit <= 0 {
		limit = 10 << 20
	}
	limit = capped(limit, hardMaxVideo)
	snap, err := io.ReadAll(io.LimitReader(rc, limit))
	if err != nil {
		// On read error (e.g. truncated chunked encoding) return what we did
		// get and drain the remainder as empty, so callers do not bypass
		// classification by triggering an error path that returns the
		// unbounded original body. Truncation is intentional for bypass
		// prevention, but we metric/log it so FailClosed can decide.
		slog.Warn("peekBody read error", "err", err, "bytes", len(snap))
		peekBodyErrors.Inc()
		if snap == nil {
			snap = []byte{}
		}
		return snap, io.NopCloser(bytes.NewReader(snap))
	}
	return snap, io.NopCloser(io.MultiReader(bytes.NewReader(snap), rc))
}

// limitFor returns the per-content-type byte cap. Falls back to the legacy
// MaxInspectBytes when a per-type cap isn't configured. Caps at hard max
// even when configured to unlimited. For generic/unknown CTs returns the
// max inspect bytes so sniffed content is still buffered sufficiently.
func (h *Handler) limitFor(ct string) int64 {
	switch {
	case isImage(ct) && h.cfg.MaxImageBytes > 0:
		return capped(h.cfg.MaxImageBytes, hardMaxImage)
	case isVideo(ct) && h.cfg.MaxVideoBytes > 0:
		return capped(h.cfg.MaxVideoBytes, hardMaxVideo)
	case textextract.IsSupported(ct) && h.cfg.MaxTextBytes > 0:
		return capped(h.cfg.MaxTextBytes, hardMaxText)
	}
	// Generic/empty CT: may be sniffed as image/text later, so use the
	// largest applicable cap rather than the minimal legacy value.
	if ct == "" || strings.Contains(strings.ToLower(ct), "octet-stream") {
		if h.cfg.MaxImageBytes > 0 {
			return capped(h.cfg.MaxImageBytes, hardMaxImage)
		}
	}
	return capped(h.cfg.MaxInspectBytes, hardMaxVideo)
}

// ---------------------------------------------------------------------------
// Content-type predicates
// ---------------------------------------------------------------------------

func shouldInspect(ct string) bool {
	return isImage(ct) || isVideo(ct) || textextract.IsSupported(ct)
}

// shouldInspectWithBody falls back to magic-byte sniffing when the server
// lies about Content-Type (e.g. NSFW JPEG served as application/octet-stream).
// This defends the bypass where attacker sets generic CT to skip classifiers (#3).
func shouldInspectWithBody(ct string, body []byte) bool {
	if shouldInspect(ct) {
		return true
	}
	// Generic / missing / misleading content-types -> sniff body.
	ct = strings.ToLower(strings.TrimSpace(strings.Split(ct, ";")[0]))
	if ct == "" || ct == "application/octet-stream" || ct == "application/binary" ||
		ct == "text/plain" || ct == "binary/octet-stream" {
		if hasImageMagic(body) {
			return true
		}
		// HTML sniff
		trimmed := strings.TrimSpace(strings.ToLower(string(body[:min(512, len(body))])))
		if strings.HasPrefix(trimmed, "<!doctype") || strings.HasPrefix(trimmed, "<html") ||
			strings.HasPrefix(trimmed, "<head") {
			return true
		}
		// JSON sniff
		if strings.HasPrefix(trimmed, "{") || strings.HasPrefix(trimmed, "[") {
			return true
		}
	}
	return false
}

func looksLikeImage(body []byte) bool {
	// Use shared magic helper; keep WEBP/BMP strict check for hasImageMagic,
	// but also accept WEBP variant which is already covered by RIFF.
	return hasImageMagic(body)
}

func effectiveIsImage(ct string, body []byte) bool {
	if isImage(ct) {
		return true
	}
	return len(body) > 0 && looksLikeImage(body)
}

func effectiveIsText(ct string, body []byte) bool {
	if textextract.IsSupported(ct) {
		return true
	}
	if len(body) == 0 {
		return false
	}
	trimmed := strings.TrimSpace(strings.ToLower(string(body[:min(512, len(body))])))
	return strings.HasPrefix(trimmed, "<!doctype") || strings.HasPrefix(trimmed, "<html") ||
		strings.HasPrefix(trimmed, "<head") || strings.HasPrefix(trimmed, "{") || strings.HasPrefix(trimmed, "[")
}

func isImage(ct string) bool {
	return strings.HasPrefix(strings.ToLower(strings.Split(ct, ";")[0]), "image/")
}

func isVideo(ct string) bool {
	return strings.HasPrefix(strings.ToLower(strings.Split(ct, ";")[0]), "video/")
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

	for cur := bare; ; {
		if h.bypass[cur] {
			return true
		}
		idx := strings.IndexByte(cur, '.')
		if idx < 0 {
			break
		}
		cur = cur[idx+1:]
	}
	return false
}

// ---------------------------------------------------------------------------
// Block page
// ---------------------------------------------------------------------------

func writeBlockPage(w http.ResponseWriter, rawURL string, reason string) {
	reason = nonEmptyReason(reason, "unspecified_block")
	w.Header().Set("Content-Type", "text/html; charset=utf-8")
	markNoStore(w.Header())
	w.WriteHeader(http.StatusForbidden)
	_, _ = fmt.Fprintf(w, `<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>Blocked — see-no-evil</title></head>
<body>
<h1>This page has been blocked</h1>
<p>URL: <code>%s</code></p>
<p>Reason: <code>%s</code></p>
<p>If you think this is a mistake, contact your administrator.</p>
</body>
</html>`, html.EscapeString(rawURL), html.EscapeString(reason))
}

func writeBlockedResponse(w http.ResponseWriter, rawURL string, reason string, contentType string, body []byte) {
	if isImage(contentType) {
		writeBlockImage(w, reason, body)
		return
	}
	writeBlockPage(w, rawURL, reason)
}

func writeBlockImage(w http.ResponseWriter, reason string, raw []byte) {
	reason = nonEmptyReason(reason, "unspecified_block")
	width, height := blockedImageDimensions(raw)
	body := blockedImageSVG(width, height, reason)
	w.Header().Set("Content-Type", "image/svg+xml; charset=utf-8")
	w.Header().Set("Content-Length", strconv.Itoa(len(body)))
	w.Header().Set("X-See-No-Evil-Blocked", "true")
	markNoStore(w.Header())
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(body)
}

func blockedImageDimensions(raw []byte) (int, int) {
	if cfg, _, err := image.DecodeConfig(bytes.NewReader(raw)); err == nil && cfg.Width > 0 && cfg.Height > 0 {
		return cfg.Width, cfg.Height
	}
	if width, height, ok := avifDimensions(raw); ok {
		return width, height
	}
	if width, height, ok := svgDimensions(raw); ok {
		return width, height
	}
	return 640, 360
}

func avifDimensions(raw []byte) (int, int, bool) {
	return findISOBMFFDimensions(raw, 0)
}

func findISOBMFFDimensions(raw []byte, depth int) (int, int, bool) {
	if depth > 8 {
		return 0, 0, false
	}
	for offset := 0; offset+8 <= len(raw); {
		size := uint64(binary.BigEndian.Uint32(raw[offset : offset+4]))
		boxType := string(raw[offset+4 : offset+8])
		headerSize := uint64(8)
		switch size {
		case 1:
			if offset+16 > len(raw) {
				return 0, 0, false
			}
			size = binary.BigEndian.Uint64(raw[offset+8 : offset+16])
			headerSize = 16
		case 0:
			size = uint64(len(raw) - offset)
		}
		if size < headerSize || uint64(offset)+size > uint64(len(raw)) {
			return 0, 0, false
		}
		payloadStart := offset + int(headerSize)
		payloadEnd := offset + int(size)
		payload := raw[payloadStart:payloadEnd]

		if boxType == "ispe" && len(payload) >= 12 {
			width := int(binary.BigEndian.Uint32(payload[4:8]))
			height := int(binary.BigEndian.Uint32(payload[8:12]))
			if width > 0 && height > 0 {
				return width, height, true
			}
		}

		childPayload := payload
		if boxType == "meta" {
			if len(payload) < 4 {
				offset += int(size)
				continue
			}
			childPayload = payload[4:]
		}
		if isContainerBox(boxType) {
			if width, height, ok := findISOBMFFDimensions(childPayload, depth+1); ok {
				return width, height, true
			}
		}

		offset += int(size)
	}
	return 0, 0, false
}

func isContainerBox(boxType string) bool {
	switch boxType {
	case "meta", "iprp", "ipco":
		return true
	default:
		return false
	}
}

func blockedImageSVG(width, height int, reason string) []byte {
	if width <= 0 {
		width = 640
	}
	if height <= 0 {
		height = 360
	}
	fontSize := clampInt(minInt(width/12, height/5), 14, 48)
	subSize := clampInt(fontSize/2, 10, 20)
	pad := clampInt(minInt(width, height)/18, 10, 32)
	textY := height/2 - fontSize/3
	subY := height/2 + fontSize
	if textY < pad+fontSize {
		textY = pad + fontSize
	}
	if subY > height-pad {
		subY = height - pad
	}

	return []byte(fmt.Sprintf(`<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" viewBox="0 0 %d %d" role="img" aria-label="see no evil blocked">
	<rect width="100%%" height="100%%" fill="#111827"/>
	<rect x="%d" y="%d" width="%d" height="%d" rx="6" fill="none" stroke="#f97316" stroke-width="2" opacity="0.9"/>
	<text x="50%%" y="%d" text-anchor="middle" font-family="Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="%d" font-weight="700" fill="#f9fafb">see no evil blocked</text>
	<text x="50%%" y="%d" text-anchor="middle" font-family="Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif" font-size="%d" fill="#d1d5db">%s</text>
</svg>`, width, height, width, height, pad, pad, maxInt(width-2*pad, 1), maxInt(height-2*pad, 1), textY, fontSize, subY, subSize, html.EscapeString(reason)))
}

func svgDimensions(raw []byte) (int, int, bool) {
	type svgRoot struct {
		XMLName xml.Name `xml:"svg"`
		Width   string   `xml:"width,attr"`
		Height  string   `xml:"height,attr"`
		ViewBox string   `xml:"viewBox,attr"`
	}
	var root svgRoot
	if err := xml.NewDecoder(bytes.NewReader(raw)).Decode(&root); err != nil || root.XMLName.Local != "svg" {
		return 0, 0, false
	}
	width, widthOK := parseSVGLength(root.Width)
	height, heightOK := parseSVGLength(root.Height)
	if widthOK && heightOK {
		return width, height, true
	}
	parts := strings.Fields(strings.ReplaceAll(root.ViewBox, ",", " "))
	if len(parts) == 4 {
		vbWidth, vbWidthOK := parseSVGLength(parts[2])
		vbHeight, vbHeightOK := parseSVGLength(parts[3])
		if vbWidthOK && vbHeightOK {
			return vbWidth, vbHeight, true
		}
	}
	return 0, 0, false
}

func parseSVGLength(value string) (int, bool) {
	value = strings.TrimSpace(value)
	if value == "" || strings.HasSuffix(value, "%") {
		return 0, false
	}
	end := 0
	for end < len(value) {
		ch := value[end]
		if (ch < '0' || ch > '9') && ch != '.' {
			break
		}
		end++
	}
	if end == 0 {
		return 0, false
	}
	parsed, err := strconv.ParseFloat(value[:end], 64)
	if err != nil || parsed <= 0 {
		return 0, false
	}
	return int(parsed + 0.5), true
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func clampInt(v, low, high int) int {
	if v < low {
		return low
	}
	if v > high {
		return high
	}
	return v
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
