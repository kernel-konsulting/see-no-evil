// Package safesearch rewrites outbound requests to enforce SafeSearch on
// common search engines and YouTube Restricted Mode.
package safesearch

import (
	"net/http"
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
	// Also force the PREF cookie (overwriting any client-set value — a
	// pre-existing cookie must not be able to disable SafeSearch).
	q := r.URL.Query()
	q.Set("safe", "active")
	r.URL.RawQuery = q.Encode()

	// Force SafeSearch preference cookie.
	setCookie(r, "PREF", "f2=8000000")
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
	// YouTube Restricted Mode is enforced via cookies. Both must be forced
	// (overwriting client-set values) or a pre-set cookie disables the mode.
	setCookie(r, "PREF", "f2=8000000")
	setCookie(r, "VISITOR_INFO1_LIVE", "")
	// Inject the YouTube-specific restricted header (only honoured server-side).
	r.Header.Set("Youtube-Restrict", "Strict")
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

func bareHost(host string) string {
	// Strip port and lowercase.
	if i := strings.IndexByte(host, ':'); i >= 0 {
		host = host[:i]
	}
	return strings.ToLower(host)
}

func hostSuffixMatch(host, suffix string) bool {
	h := bareHost(host)
	s := strings.ToLower(suffix)
	return h == s || strings.HasSuffix(h, "."+s)
}

func isGoogle(host string) bool {
	h := bareHost(host)
	if strings.Contains(h, "google.") {
		return true
	}
	return hostSuffixMatch(host, "googleapis.com") ||
		strings.HasSuffix(h, ".googleusercontent.com") ||
		hostSuffixMatch(host, "gstatic.com")
}

func isBing(host string) bool {
	return hostSuffixMatch(host, "bing.com")
}

func isDDG(host string) bool {
	return hostSuffixMatch(host, "duckduckgo.com")
}

func isYouTube(host string) bool {
	return hostSuffixMatch(host, "youtube.com") || hostSuffixMatch(host, "youtu.be") ||
		hostSuffixMatch(host, "youtube-nocookie.com") || hostSuffixMatch(host, "youtube-ui.l.google.com")
}

// setCookie writes name=value into the request's Cookie header, removing any
// existing cookie with the same name first. Enforced cookies must overwrite —
// a client that pre-sets its own PREF/VISITOR_INFO1_LIVE cookie would
// otherwise keep SafeSearch / Restricted Mode disabled. Values are written
// verbatim (cookie values must not be percent-encoded — Go's parser would
// hand the escaped form back to the upstream server).
func setCookie(r *http.Request, name, value string) {
	var kept []string
	for _, c := range r.Cookies() {
		if c.Name == name {
			continue
		}
		kept = append(kept, c.Name+"="+c.Value)
	}
	kept = append(kept, name+"="+value)
	r.Header.Set("Cookie", strings.Join(kept, "; "))
}
