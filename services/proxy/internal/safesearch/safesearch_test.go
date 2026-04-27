package safesearch_test

import (
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/safesearch"
)

func TestGoogleSafeSearch(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "http://www.google.com/search?q=hello", nil)
	r.Host = "www.google.com"

	safesearch.RewriteRequest(r, safesearch.Config{Google: true})

	if got := r.URL.Query().Get("safe"); got != "active" {
		t.Errorf("expected safe=active, got %q", got)
	}
}

func TestBingSafeSearch(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "http://www.bing.com/search?q=hello", nil)
	r.Host = "www.bing.com"

	safesearch.RewriteRequest(r, safesearch.Config{Bing: true})

	if got := r.URL.Query().Get("adlt"); got != "strict" {
		t.Errorf("expected adlt=strict, got %q", got)
	}
}

func TestDDGSafeSearch(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "https://duckduckgo.com/?q=hello", nil)
	r.Host = "duckduckgo.com"

	safesearch.RewriteRequest(r, safesearch.Config{DuckDuckGo: true})

	if got := r.URL.Query().Get("kp"); got != "1" {
		t.Errorf("expected kp=1, got %q", got)
	}
}

func TestYouTubeRestricted(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "https://www.youtube.com/watch?v=abc", nil)
	r.Host = "www.youtube.com"

	safesearch.RewriteRequest(r, safesearch.Config{YouTubeRestricted: true})

	if got := r.Header.Get("Youtube-Restrict"); got != "Strict" {
		t.Errorf("expected Youtube-Restrict: Strict, got %q", got)
	}
}

func TestNoRewriteWhenDisabled(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "http://www.google.com/search?q=hello", nil)
	r.Host = "www.google.com"

	safesearch.RewriteRequest(r, safesearch.Config{}) // all disabled

	if got := r.URL.Query().Get("safe"); got != "" {
		t.Errorf("expected no safe param when disabled, got %q", got)
	}
}
