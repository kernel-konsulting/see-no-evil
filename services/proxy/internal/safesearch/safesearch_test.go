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

func TestGoogleSafeSearchOverwritesClientCookie(t *testing.T) {
	// A client that pre-sets PREF to disable SafeSearch must be overridden,
	// not left alone — otherwise SafeSearch is trivially bypassed.
	r := httptest.NewRequest(http.MethodGet, "http://www.google.com/search?q=hello", nil)
	r.Host = "www.google.com"
	r.Header.Set("Cookie", "PREF=f2=0000000; SID=abc123")

	safesearch.RewriteRequest(r, safesearch.Config{Google: true})

	cookies := r.Cookies()
	var pref string
	for _, c := range cookies {
		if c.Name == "PREF" {
			pref = c.Value
		}
	}
	if pref != "f2=8000000" {
		t.Errorf("PREF = %q, want f2=8000000 (client value must be overwritten)", pref)
	}
	// Unrelated cookies survive.
	foundSID := false
	for _, c := range cookies {
		if c.Name == "SID" {
			foundSID = true
		}
	}
	if !foundSID {
		t.Error("unrelated cookie SID was dropped")
	}
}

func TestYouTubeRestrictedOverwritesClientCookie(t *testing.T) {
	r := httptest.NewRequest(http.MethodGet, "https://www.youtube.com/watch?v=abc", nil)
	r.Host = "www.youtube.com"
	r.Header.Set("Cookie", "VISITOR_INFO1_LIVE=someValue; PREF=f2=0000000")

	safesearch.RewriteRequest(r, safesearch.Config{YouTubeRestricted: true})

	var pref, visitor string
	for _, c := range r.Cookies() {
		switch c.Name {
		case "PREF":
			pref = c.Value
		case "VISITOR_INFO1_LIVE":
			visitor = c.Value
		}
	}
	if pref != "f2=8000000" {
		t.Errorf("PREF = %q, want f2=8000000", pref)
	}
	if visitor != "" {
		t.Errorf("VISITOR_INFO1_LIVE = %q, want empty (forced)", visitor)
	}
}
