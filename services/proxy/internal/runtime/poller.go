// Package runtime polls the API's /v1/runtime endpoint for live settings.
//
// The proxy honors:
//   - inspect.{image,video,text,url}  — skip classifier calls when false
//   - lists.global_*                  — passed through to the API on /v1/decide
//
// We poll instead of websocket because the API may not be up at proxy startup
// and a poller naturally retries.
package runtime

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"sync/atomic"
	"time"
)

type Settings struct {
	Inspect struct {
		Image  bool `json:"image"`
		Video  bool `json:"video"`
		Text   bool `json:"text"`
		Domain bool `json:"domain"`
		URL    bool `json:"url"`
	} `json:"inspect"`
	Lists struct {
		GlobalAllowDomains []string `json:"global_allow_domains"`
		GlobalDenyDomains  []string `json:"global_deny_domains"`
		GlobalDenyKeywords []string `json:"global_deny_keywords"`
	} `json:"lists"`
	Text struct {
		NSFWThreshold float32 `json:"nsfw_threshold"`
	} `json:"text"`
}

func defaults() *Settings {
	s := &Settings{}
	s.Inspect.Image = true
	s.Inspect.Video = true
	s.Inspect.Text = true
	s.Inspect.Domain = true
	s.Inspect.URL = true
	s.Text.NSFWThreshold = 0.5
	return s
}

// Poller holds the latest settings, refreshed from the API every Interval.
// Always returns non-nil; if the API is unreachable it falls back to defaults.
type Poller struct {
	apiBase  string
	interval time.Duration
	current  atomic.Pointer[Settings]
	client   *http.Client
}

func NewPoller(apiBase string, interval time.Duration) *Poller {
	if interval <= 0 {
		interval = 30 * time.Second
	}
	p := &Poller{
		apiBase:  apiBase,
		interval: interval,
		client:   &http.Client{Timeout: 5 * time.Second},
	}
	p.current.Store(defaults())
	return p
}

func (p *Poller) Get() *Settings {
	s := p.current.Load()
	if s == nil {
		return defaults()
	}
	return s
}

func (p *Poller) fetch(ctx context.Context) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, p.apiBase+"/v1/runtime", nil)
	if err != nil {
		return
	}
	resp, err := p.client.Do(req)
	if err != nil {
		slog.Debug("runtime poll failed", "err", err)
		return
	}
	defer func() { _ = resp.Body.Close() }()
	if resp.StatusCode != http.StatusOK {
		return
	}
	var s Settings
	if err := json.NewDecoder(resp.Body).Decode(&s); err != nil {
		return
	}
	p.current.Store(&s)
}

// Run blocks; cancel ctx to stop. Performs an immediate fetch then polls.
func (p *Poller) Run(ctx context.Context) {
	p.fetch(ctx)
	t := time.NewTicker(p.interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			p.fetch(ctx)
		}
	}
}
