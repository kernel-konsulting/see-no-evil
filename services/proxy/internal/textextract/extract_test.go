package textextract_test

import (
	"strings"
	"testing"

	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/textextract"
)

func TestExtractHTMLSkipsScriptStyle(t *testing.T) {
	body := []byte(`<!doctype html><html><head>
<script>var bad="explicit content goes here yes";</script>
<style>.x{content:"explicit content goes here yes";}</style>
</head><body>
<p>This is a longer paragraph that should be picked up.</p>
<p>short</p>
<noscript>noscript text we should ignore entirely</noscript>
</body></html>`)
	segs := textextract.Extract("text/html; charset=utf-8", body)
	if len(segs) != 1 {
		t.Fatalf("expected exactly 1 segment, got %d: %#v", len(segs), segs)
	}
	if !strings.Contains(segs[0].Text, "longer paragraph") {
		t.Errorf("unexpected segment text: %q", segs[0].Text)
	}
	if segs[0].Path != "html:p" {
		t.Errorf("expected path html:p, got %q", segs[0].Path)
	}
}

func TestExtractJSONOnlyNaturalLanguage(t *testing.T) {
	body := []byte(`{
		"id": "UCabcdefghijklmnopqrst",
		"url": "https://example.com/foo/bar/baz",
		"title": "How to make sourdough bread at home",
		"items": [
			{"description": "A short text"},
			{"description": "This is a long enough description with spaces"}
		]
	}`)
	segs := textextract.Extract("application/json", body)
	if len(segs) != 2 {
		t.Fatalf("expected 2 segments, got %d: %#v", len(segs), segs)
	}
	texts := []string{segs[0].Text, segs[1].Text}
	wantA := "How to make sourdough bread at home"
	wantB := "This is a long enough description with spaces"
	hasA, hasB := false, false
	for _, txt := range texts {
		if txt == wantA {
			hasA = true
		}
		if txt == wantB {
			hasB = true
		}
	}
	if !hasA || !hasB {
		t.Errorf("missing expected segments: hasA=%v hasB=%v texts=%v", hasA, hasB, texts)
	}
}

func TestExtractUnsupportedReturnsNil(t *testing.T) {
	if got := textextract.Extract("image/png", []byte("data")); got != nil {
		t.Errorf("expected nil for image/png, got %v", got)
	}
	if got := textextract.Extract("", nil); got != nil {
		t.Errorf("expected nil for empty body, got %v", got)
	}
}

func TestIsSupported(t *testing.T) {
	cases := map[string]bool{
		"text/html":               true,
		"text/html; charset=utf8": true,
		"application/json":        true,
		"application/ld+json":     true,
		"application/xhtml+xml":   true,
		"text/plain":              false,
		"image/jpeg":              false,
		"":                        false,
	}
	for ct, want := range cases {
		if got := textextract.IsSupported(ct); got != want {
			t.Errorf("IsSupported(%q) = %v, want %v", ct, got, want)
		}
	}
}

// ---------------------------------------------------------------------------
// Strip
// ---------------------------------------------------------------------------

const banned = "Banned content phrase that triggers redaction here"

func redactBanned(s string) bool { return strings.Contains(s, "Banned content") }

func TestStripHTMLReplacesFlaggedTextNodes(t *testing.T) {
	body := []byte(`<!doctype html><html><body>
<p>This paragraph is perfectly fine and stays put.</p>
<p>` + banned + `</p>
</body></html>`)
	out, changed := textextract.Strip("text/html", body, redactBanned, "[redacted]")
	if !changed {
		t.Fatal("expected changed=true")
	}
	if strings.Contains(string(out), "Banned content") {
		t.Errorf("expected banned text to be removed, got: %s", out)
	}
	if !strings.Contains(string(out), "[redacted]") {
		t.Errorf("expected replacement marker, got: %s", out)
	}
	if !strings.Contains(string(out), "perfectly fine") {
		t.Errorf("expected innocent text to survive, got: %s", out)
	}
}

func TestStripJSONReplacesFlaggedStrings(t *testing.T) {
	body := []byte(`{"title":"` + banned + `","ok":"This is a perfectly safe sentence."}`)
	out, changed := textextract.Strip("application/json", body, redactBanned, "[redacted]")
	if !changed {
		t.Fatal("expected changed=true")
	}
	if strings.Contains(string(out), "Banned content") {
		t.Errorf("expected banned text to be removed, got: %s", out)
	}
	if !strings.Contains(string(out), "[redacted]") {
		t.Errorf("expected replacement marker, got: %s", out)
	}
	if !strings.Contains(string(out), "perfectly safe") {
		t.Errorf("expected innocent text to survive, got: %s", out)
	}
}

func TestStripNoMatchReturnsOriginal(t *testing.T) {
	body := []byte(`<p>This is a perfectly safe paragraph here</p>`)
	out, changed := textextract.Strip("text/html", body, redactBanned, "[redacted]")
	if changed {
		t.Errorf("expected changed=false")
	}
	if &out[0] != &body[0] {
		// Not strictly required, but documents intent: when nothing changed we
		// should hand back the original byte slice.
		t.Logf("note: returned a different backing array; not a failure")
	}
}

func TestStripUnsupportedReturnsOriginal(t *testing.T) {
	body := []byte("hello")
	out, changed := textextract.Strip("text/plain", body, redactBanned, "x")
	if changed {
		t.Errorf("expected changed=false for text/plain")
	}
	if string(out) != "hello" {
		t.Errorf("expected unchanged body, got %q", out)
	}
}
