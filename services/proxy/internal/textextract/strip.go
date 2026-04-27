package textextract

import (
	"bytes"
	"encoding/json"
	"strings"

	"golang.org/x/net/html"
)

// Predicate decides whether a given segment of text should be redacted.
// Implementations should be safe to call concurrently.
type Predicate func(text string) bool

// Strip rewrites body by replacing every text segment for which redact
// returns true with replacement.
//
// Returns the new body and a boolean indicating whether anything was changed.
// On parse failure or unsupported content type, the original body is returned
// unchanged with changed=false.
func Strip(contentType string, body []byte, redact Predicate, replacement string) ([]byte, bool) {
	if len(body) == 0 || redact == nil {
		return body, false
	}
	mt := mediaType(contentType)
	switch {
	case strings.HasPrefix(mt, "text/html"), mt == "application/xhtml+xml":
		return stripHTML(body, redact, replacement)
	case mt == "application/json", strings.HasSuffix(mt, "+json"):
		return stripJSON(body, redact, replacement)
	default:
		return body, false
	}
}

// ---------------------------------------------------------------------------
// HTML
// ---------------------------------------------------------------------------

func stripHTML(body []byte, redact Predicate, replacement string) ([]byte, bool) {
	doc, err := html.Parse(bytes.NewReader(body))
	if err != nil {
		return body, false
	}
	changed := stripHTMLNode(doc, redact, replacement)
	if !changed {
		return body, false
	}
	var buf bytes.Buffer
	if err := html.Render(&buf, doc); err != nil {
		return body, false
	}
	return buf.Bytes(), true
}

func stripHTMLNode(n *html.Node, redact Predicate, replacement string) bool {
	if n.Type == html.ElementNode && htmlSkipTags[n.DataAtom] {
		return false
	}
	changed := false
	if n.Type == html.TextNode {
		t := strings.TrimSpace(n.Data)
		if len([]rune(t)) >= MinSegmentLen && redact(t) {
			n.Data = replacement
			return true
		}
	}
	for c := n.FirstChild; c != nil; c = c.NextSibling {
		if stripHTMLNode(c, redact, replacement) {
			changed = true
		}
	}
	return changed
}

// ---------------------------------------------------------------------------
// JSON
// ---------------------------------------------------------------------------

func stripJSON(body []byte, redact Predicate, replacement string) ([]byte, bool) {
	var v any
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		return body, false
	}
	changed := false
	v = stripJSONValue(v, redact, replacement, &changed)
	if !changed {
		return body, false
	}
	out, err := json.Marshal(v)
	if err != nil {
		return body, false
	}
	return out, true
}

func stripJSONValue(v any, redact Predicate, replacement string, changed *bool) any {
	switch t := v.(type) {
	case map[string]any:
		for k, vv := range t {
			t[k] = stripJSONValue(vv, redact, replacement, changed)
		}
		return t
	case []any:
		for i, vv := range t {
			t[i] = stripJSONValue(vv, redact, replacement, changed)
		}
		return t
	case string:
		s := strings.TrimSpace(t)
		if looksLikeNaturalLanguage(s) && redact(s) {
			*changed = true
			return replacement
		}
		return t
	default:
		return v
	}
}
