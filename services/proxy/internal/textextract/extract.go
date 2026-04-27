// Package textextract pulls natural-language text segments out of HTML and
// JSON response bodies so they can be fed to the text classifier without
// drowning it in markup or token IDs.
//
// It also supports rebuilding the body with flagged segments replaced
// ("strip" mode), which lets us serve a sanitised version of an otherwise-OK
// page rather than blocking it outright.
package textextract

import (
	"bytes"
	"encoding/json"
	"strings"

	"golang.org/x/net/html"
	"golang.org/x/net/html/atom"
)

// Segment is one chunk of natural-language text extracted from a body.
type Segment struct {
	// Text is the segment, already trimmed of surrounding whitespace.
	Text string
	// Path is a short human-readable hint used for logging
	// (e.g. "html:p", "json:items[3].title").
	Path string
}

// MinSegmentLen is the minimum character length a segment must have to be
// worth classifying. Anything shorter is almost certainly a label, ID, or
// punctuation and would only generate noise for the classifier.
const MinSegmentLen = 16

// htmlSkipTags are tags whose text contents are never natural language.
var htmlSkipTags = map[atom.Atom]bool{
	atom.Script:   true,
	atom.Style:    true,
	atom.Noscript: true,
	atom.Template: true,
	atom.Svg:      true,
}

// IsSupported reports whether Extract knows how to handle the given
// Content-Type.
func IsSupported(contentType string) bool {
	mt := mediaType(contentType)
	switch {
	case strings.HasPrefix(mt, "text/html"), mt == "application/xhtml+xml":
		return true
	case mt == "application/json", strings.HasSuffix(mt, "+json"):
		return true
	default:
		return false
	}
}

// Extract returns the natural-language text segments from body. The returned
// slice is empty (not nil) for unsupported content types or unparseable
// bodies. Segments shorter than MinSegmentLen are dropped.
func Extract(contentType string, body []byte) []Segment {
	if len(body) == 0 {
		return nil
	}
	mt := mediaType(contentType)
	switch {
	case strings.HasPrefix(mt, "text/html"), mt == "application/xhtml+xml":
		return extractHTML(body)
	case mt == "application/json", strings.HasSuffix(mt, "+json"):
		return extractJSON(body)
	default:
		return nil
	}
}

// ---------------------------------------------------------------------------
// HTML
// ---------------------------------------------------------------------------

func extractHTML(body []byte) []Segment {
	doc, err := html.Parse(bytes.NewReader(body))
	if err != nil {
		return nil
	}
	var out []Segment
	walkHTML(doc, &out, "")
	return out
}

func walkHTML(n *html.Node, out *[]Segment, parentTag string) {
	if n.Type == html.ElementNode && htmlSkipTags[n.DataAtom] {
		return
	}
	if n.Type == html.TextNode {
		t := strings.TrimSpace(n.Data)
		if len([]rune(t)) >= MinSegmentLen {
			tag := parentTag
			if tag == "" {
				tag = "text"
			}
			*out = append(*out, Segment{Text: t, Path: "html:" + tag})
		}
	}
	tag := parentTag
	if n.Type == html.ElementNode {
		tag = n.Data
	}
	for c := n.FirstChild; c != nil; c = c.NextSibling {
		walkHTML(c, out, tag)
	}
}

// ---------------------------------------------------------------------------
// JSON
// ---------------------------------------------------------------------------

func extractJSON(body []byte) []Segment {
	var v any
	dec := json.NewDecoder(bytes.NewReader(body))
	dec.UseNumber()
	if err := dec.Decode(&v); err != nil {
		return nil
	}
	var out []Segment
	walkJSON(v, "json:", &out)
	return out
}

func walkJSON(v any, path string, out *[]Segment) {
	switch t := v.(type) {
	case map[string]any:
		for k, vv := range t {
			walkJSON(vv, path+"."+k, out)
		}
	case []any:
		for i, vv := range t {
			walkJSON(vv, path+"["+itoa(i)+"]", out)
			// Cap the per-array fan-out: classifying every item in a long
			// list of comments is wasteful — the first 32 are representative.
			if i >= 31 {
				break
			}
		}
	case string:
		s := strings.TrimSpace(t)
		if !looksLikeNaturalLanguage(s) {
			return
		}
		*out = append(*out, Segment{Text: s, Path: path})
	}
}

func looksLikeNaturalLanguage(s string) bool {
	if len([]rune(s)) < MinSegmentLen {
		return false
	}
	// Reject obvious non-prose: URLs, base64-ish blobs, single tokens.
	if strings.HasPrefix(s, "http://") || strings.HasPrefix(s, "https://") {
		return false
	}
	if !strings.ContainsRune(s, ' ') {
		return false
	}
	return true
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

// mediaType returns the media type portion of a Content-Type header, lowercased.
func mediaType(contentType string) string {
	ct := strings.TrimSpace(contentType)
	if i := strings.IndexByte(ct, ';'); i >= 0 {
		ct = ct[:i]
	}
	return strings.ToLower(strings.TrimSpace(ct))
}

func itoa(i int) string {
	// Tiny helper to avoid pulling in strconv just for paths.
	if i == 0 {
		return "0"
	}
	neg := false
	if i < 0 {
		neg = true
		i = -i
	}
	var buf [20]byte
	pos := len(buf)
	for i > 0 {
		pos--
		buf[pos] = byte('0' + i%10)
		i /= 10
	}
	if neg {
		pos--
		buf[pos] = '-'
	}
	return string(buf[pos:])
}
