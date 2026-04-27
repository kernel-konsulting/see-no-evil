// Package safesearch rewrites outbound requests to enforce SafeSearch on
// common search engines and YouTube Restricted Mode.
package safesearch

import (
	"net/http"
	"net/url"
	"strings"
)

// Config mirrors the proxy.safesearch section of config.yaml.
type Config struct {
	Google            bool
	Bing              bool
	DuckDuckGo        bool
	YouTubeRestricted bool
}

// RewriteRequest modifies r in-place to enforce the configured SafeSearch
// settings.  It is called for every outbound request the proxy observes.
func RewriteRequest(r *http.Request, cfg Config) {
	host := strings.ToLower(r.Host)

	switch {
	case cfg.Google && isGoogle(host):
		enforceGoogleSafeSearch(r)
	case cfg.Bing && isBing(host):
		enforceBingSafeSearch(r)
	case cfg.DuckDuckGo && isDDG(host):
		enforceDDGSafeSearch(r)
	case cfg.YouTubeRestricted && isYouTube(host):
		enforceYouTubeRestricted(r)
	}
}

// ---------------------------------------------------------------------------
// Per-engine enforcement
// ---------------------------------------------------------------------------

func enforceGoogleSafeSearch(r *http.Request) {
	// Google SafeSearch: ?safe=active on web search.
	// Also set Pref cookie for persistence (belt and braces).
	q := r.URL.Query()
	q.Set("safe", "active")
	r.URL.RawQuery = q.Encode()

	// Inject SafeSearch preference cookie.
	setCookieIfAbsent(r, "PREF", "f2=8000000")
}

func enforceBingSafeSearch(r *http.Request) {
	q := r.URL.Query()
	q.Set("adlt", "strict")
	r.URL.RawQuery = q.Encode()
}

func enforceDDGSafeSearch(r *http.Request) {
	q := r.URL.Query()
	q.Set("kp", "1") // 1 = moderate, -1 = off, -2 = strict; use moderate
	r.URL.RawQuery = q.Encode()
}

func enforceYouTubeRestricted(r *http.Request) {
	// YouTube Restricted Mode is enforced via a cookie.
	// PREF with f2=8000000 sets SafeSearch; Restricted Mode needs:
	setCookieIfAbsent(r, "PREF", "f2=8000000")
	// The Restricted Mode toggle lives in a separate YT cookie.
	setCookieIfAbsent(r, "VISITOR_INFO1_LIVE", "")
	// Inject the YouTube-specific restricted header (only honoured server-side).
	r.Header.Set("Youtube-Restrict", "Strict")
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func isGoogle(host string) bool {
	return strings.Contains(host, "google.") || strings.Contains(host, "googleapis.com")
}

func isBing(host string) bool {
	return strings.Contains(host, "bing.com")
}

func isDDG(host string) bool {
	return strings.Contains(host, "duckduckgo.com")
}

func isYouTube(host string) bool {
	return strings.Contains(host, "youtube.com") || strings.Contains(host, "youtu.be")
}

func setCookieIfAbsent(r *http.Request, name, value string) {
	for _, c := range r.Cookies() {
		if c.Name == name {
			return
		}
	}
	existing := r.Header.Get("Cookie")
	newCookie := url.QueryEscape(name) + "=" + url.QueryEscape(value)
	if existing == "" {
		r.Header.Set("Cookie", newCookie)
	} else {
		r.Header.Set("Cookie", existing+"; "+newCookie)
	}
}
