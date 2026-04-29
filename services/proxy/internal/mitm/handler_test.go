package mitm

import (
	"bytes"
	"image"
	"image/color"
	"image/png"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestCopyHeadersStripsAltSvc(t *testing.T) {
	src := http.Header{}
	src.Set("Content-Type", "image/avif")
	src.Set("Alt-Svc", `h3=":443"; ma=86400`)
	src.Set("Alt-Used", "www.example.test")

	dst := http.Header{}
	copyHeaders(dst, src)

	if got := dst.Get("Content-Type"); got != "image/avif" {
		t.Fatalf("Content-Type = %q, want image/avif", got)
	}
	if got := dst.Get("Alt-Svc"); got != "" {
		t.Fatalf("Alt-Svc = %q, want stripped", got)
	}
	if got := dst.Get("Alt-Used"); got != "" {
		t.Fatalf("Alt-Used = %q, want stripped", got)
	}
}

func TestMarkNoStore(t *testing.T) {
	h := http.Header{}
	h.Set("Cache-Control", "max-age=157680000")
	h.Set("ETag", `"abc"`)
	h.Set("Last-Modified", "Tue, 28 Apr 2026 04:00:00 GMT")

	markNoStore(h)

	if got := h.Get("Cache-Control"); got != "no-store, private" {
		t.Fatalf("Cache-Control = %q, want no-store, private", got)
	}
	if got := h.Get("Pragma"); got != "no-cache" {
		t.Fatalf("Pragma = %q, want no-cache", got)
	}
	if got := h.Get("Expires"); got != "0" {
		t.Fatalf("Expires = %q, want 0", got)
	}
	if got := h.Get("ETag"); got != "" {
		t.Fatalf("ETag = %q, want stripped", got)
	}
	if got := h.Get("Last-Modified"); got != "" {
		t.Fatalf("Last-Modified = %q, want stripped", got)
	}
}

func TestWriteBlockPageIsNoStore(t *testing.T) {
	recorder := httptest.NewRecorder()

	writeBlockPage(recorder, "https://example.test/image.jpg", "classifier:image:sexy")

	if recorder.Code != http.StatusForbidden {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusForbidden)
	}
	if got := recorder.Header().Get("Cache-Control"); got != "no-store, private" {
		t.Fatalf("Cache-Control = %q, want no-store, private", got)
	}
	if got := recorder.Header().Get("Pragma"); got != "no-cache" {
		t.Fatalf("Pragma = %q, want no-cache", got)
	}
}

func TestWriteBlockedResponseForImageUsesSizedSVG(t *testing.T) {
	img := image.NewRGBA(image.Rect(0, 0, 320, 180))
	for y := 0; y < 180; y++ {
		for x := 0; x < 320; x++ {
			img.Set(x, y, color.RGBA{R: 30, G: 80, B: 120, A: 255})
		}
	}
	var input bytes.Buffer
	if err := png.Encode(&input, img); err != nil {
		t.Fatalf("encode source: %v", err)
	}

	recorder := httptest.NewRecorder()
	writeBlockedResponse(recorder, "https://example.test/image.png", "classifier:image:sexy", "image/png", input.Bytes())

	if recorder.Code != http.StatusOK {
		t.Fatalf("status = %d, want %d", recorder.Code, http.StatusOK)
	}
	if got := recorder.Header().Get("Content-Type"); got != "image/svg+xml; charset=utf-8" {
		t.Fatalf("Content-Type = %q, want SVG", got)
	}
	if got := recorder.Header().Get("X-See-No-Evil-Blocked"); got != "true" {
		t.Fatalf("X-See-No-Evil-Blocked = %q, want true", got)
	}
	body := recorder.Body.String()
	for _, want := range []string{`width="320"`, `height="180"`, "see no evil blocked", "classifier:image:sexy"} {
		if !strings.Contains(body, want) {
			t.Fatalf("generated SVG missing %q: %s", want, body)
		}
	}
	if got := recorder.Header().Get("Cache-Control"); got != "no-store, private" {
		t.Fatalf("Cache-Control = %q, want no-store, private", got)
	}
}

func TestBlockedImageDimensionsFromSVGViewBox(t *testing.T) {
	width, height := blockedImageDimensions([]byte(`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 123 45"></svg>`))
	if width != 123 || height != 45 {
		t.Fatalf("dimensions = %dx%d, want 123x45", width, height)
	}
}

func TestBlockedImageDimensionsFromAVIFIspe(t *testing.T) {
	makeBox := func(kind string, payload []byte) []byte {
		box := make([]byte, 8+len(payload))
		box[0] = byte(len(box) >> 24)
		box[1] = byte(len(box) >> 16)
		box[2] = byte(len(box) >> 8)
		box[3] = byte(len(box))
		copy(box[4:8], kind)
		copy(box[8:], payload)
		return box
	}
	ispePayload := []byte{
		0, 0, 0, 0,
		0, 0, 1, 64,
		0, 0, 0, 180,
	}
	raw := makeBox("meta", append([]byte{0, 0, 0, 0}, makeBox("iprp", makeBox("ipco", makeBox("ispe", ispePayload)))...))

	width, height := blockedImageDimensions(raw)
	if width != 320 || height != 180 {
		t.Fatalf("dimensions = %dx%d, want 320x180", width, height)
	}
}

func TestIsVideoSamplerFailure(t *testing.T) {
	if !isVideoSamplerFailure("video_sampler:ffmpeg_failed") {
		t.Fatal("expected ffmpeg failure to be treated as a sampler failure")
	}
	if isVideoSamplerFailure("classifier:video:porn") {
		t.Fatal("expected classifier block reason not to be treated as infra failure")
	}
}

func TestExtractYouTubeVideoID(t *testing.T) {
	cases := []struct {
		name string
		url  string
		body string
		want string
	}{
		{name: "thumbnail", url: "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", want: "dQw4w9WgXcQ"},
		{name: "webp thumbnail", url: "https://i.ytimg.com/vi_webp/dQw4w9WgXcQ/maxresdefault.webp", want: "dQw4w9WgXcQ"},
		{name: "watch", url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ", want: "dQw4w9WgXcQ"},
		{name: "shorts", url: "https://www.youtube.com/shorts/dQw4w9WgXcQ", want: "dQw4w9WgXcQ"},
		{name: "player api", url: "https://www.youtube.com/youtubei/v1/player", body: `{"videoId":"dQw4w9WgXcQ"}`, want: "dQw4w9WgXcQ"},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			req := httptest.NewRequest(http.MethodGet, tc.url, nil)
			if got := extractYouTubeVideoID(req, []byte(tc.body)); got != tc.want {
				t.Fatalf("extractYouTubeVideoID() = %q, want %q", got, tc.want)
			}
		})
	}
}

func TestBlockedYouTubeThumbnailBlocksMatchingPlayback(t *testing.T) {
	h := &Handler{blockedYouTubeVideos: make(map[string]blockedYouTubeVideo)}
	thumb := httptest.NewRequest(http.MethodGet, "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg", nil)
	h.rememberBlockedYouTubeVideo(thumb, nil, "classifier:image:nsfw")

	watch := httptest.NewRequest(http.MethodGet, "https://www.youtube.com/watch?v=dQw4w9WgXcQ", nil)
	if reason, ok := h.blockReasonForYouTubeRequest(watch, nil); !ok || !strings.Contains(reason, "classifier:image:nsfw") {
		t.Fatalf("blockReasonForYouTubeRequest() = %q, %v; want cached thumbnail block", reason, ok)
	}

	if reason, ok := h.blockReasonForYouTubeRequest(thumb, nil); ok {
		t.Fatalf("thumbnail request should keep flowing through image classifier, got block %q", reason)
	}
}
