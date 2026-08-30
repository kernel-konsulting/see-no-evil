// Package quota reports per-device activity to the API's /v1/quota/heartbeat
// endpoint so profiles with quota_minutes_per_day actually accumulate usage.
//
// The proxy cannot see client MACs, so attribution is by source IP — the API
// resolves the IP to a Device row (scanner-supplied or auto-created). A client
// is credited with one active minute for each minute window in which it sent
// at least one request through the proxy.
package quota

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync"
	"time"
)

// maxReportedMinutes bounds a single heartbeat; the API rejects anything
// above 24*60 anyway.
const maxReportedMinutes = 24 * 60

// Reporter accumulates per-IP active minutes and flushes them to the API on a
// fixed cadence. Safe for concurrent use.
type Reporter struct {
	apiBase string
	token   string
	client  *http.Client
	flushFn func(ctx context.Context, ip string, minutes int) error // overridable in tests

	mu      sync.Mutex
	active  map[string]bool // ip → traffic seen in the current minute window
	minutes map[string]int  // ip → accumulated unflushed minutes
}

// NewReporter builds a Reporter posting to apiBase/v1/quota/heartbeat.
func NewReporter(apiBase, token string) *Reporter {
	r := &Reporter{
		apiBase: apiBase,
		token:   token,
		client:  &http.Client{Timeout: 5 * time.Second},
		active:  make(map[string]bool),
		minutes: make(map[string]int),
	}
	r.flushFn = r.post
	return r
}

// NoteActivity records that ip sent traffic in the current minute window.
// Empty ips are ignored (no RemoteAddr available).
func (r *Reporter) NoteActivity(ip string) {
	if ip == "" {
		return
	}
	r.mu.Lock()
	r.active[ip] = true
	r.mu.Unlock()
}

// accumulate credits one active minute to every IP that was active during the
// previous window, then clears the window. Called once per flush interval.
// Uses map swap to hold lock for O(1).
func (r *Reporter) accumulate() {
	r.mu.Lock()
	cur := r.active
	r.active = make(map[string]bool, len(cur))
	r.mu.Unlock()
	for ip, seen := range cur {
		if seen {
			r.mu.Lock()
			r.minutes[ip]++
			r.mu.Unlock()
		}
	}
}

// flush posts each IP's accumulated minutes and zeroes them on success. On
// failure the counters are kept so the next tick retries.
func (r *Reporter) flush(ctx context.Context) {
	r.mu.Lock()
	ips := make([]string, 0, len(r.minutes))
	for ip := range r.minutes {
		ips = append(ips, ip)
	}
	r.mu.Unlock()

	for _, ip := range ips {
		r.mu.Lock()
		m := r.minutes[ip]
		r.mu.Unlock()
		if m <= 0 {
			continue
		}
		toSend := m
		if toSend > maxReportedMinutes {
			toSend = maxReportedMinutes
		}
		if err := r.flushFn(ctx, ip, toSend); err != nil {
			slog.Warn("quota heartbeat failed", "ip", ip, "minutes", toSend, "err", err)
			continue // keep counters for the next tick
		}
		slog.Debug("quota heartbeat sent", "ip", ip, "minutes", toSend)
		r.mu.Lock()
		cur := r.minutes[ip]
		if cur <= toSend {
			delete(r.minutes, ip)
		} else {
			// Capped post (e.g. 2000 minutes accumulated but only 1440 sent)
			// must leave the remainder for the next tick instead of deleting
			// it (#16). Also handles the race where accumulate ran during the
			// blocking HTTP post and bumped cur beyond m.
			r.minutes[ip] = cur - toSend
		}
		r.mu.Unlock()
	}
}

// post sends a single heartbeat. Exported for tests.
func (r *Reporter) post(ctx context.Context, ip string, minutes int) error {
	if minutes > maxReportedMinutes {
		minutes = maxReportedMinutes
	}
	body, err := json.Marshal(map[string]any{
		"client_ip": ip,
		"minutes":   minutes,
	})
	if err != nil {
		return err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, r.apiBase+"/v1/quota/heartbeat", bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Content-Type", "application/json")
	if r.token != "" {
		req.Header.Set("Authorization", "Bearer "+r.token)
	}
	resp, err := r.client.Do(req)
	if err != nil {
		return err
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return &httpError{code: resp.StatusCode}
	}
	return nil
}

type httpError struct{ code int }

func (e *httpError) Error() string { return "heartbeat returned " + http.StatusText(e.code) }

// Run accumulates and flushes every interval until ctx is cancelled.
// interval <= 0 falls back to 60s.
func (r *Reporter) Run(ctx context.Context, interval time.Duration) {
	if interval <= 0 {
		interval = 60 * time.Second
	}
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			r.accumulate()
			r.flush(ctx)
		}
	}
}
