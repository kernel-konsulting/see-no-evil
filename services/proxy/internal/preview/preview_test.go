package preview_test

import (
	"bytes"
	"encoding/base64"
	"image"
	"image/color"
	"image/png"
	"strings"
	"testing"

	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/preview"
)

func TestImagePNGRoundtrip(t *testing.T) {
	src := image.NewRGBA(image.Rect(0, 0, 800, 400))
	for y := 0; y < 400; y++ {
		for x := 0; x < 800; x++ {
			src.Set(x, y, color.RGBA{R: uint8(x % 255), G: uint8(y % 255), B: 128, A: 255})
		}
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, src); err != nil {
		t.Fatalf("encode src: %v", err)
	}

	out, ok := preview.Image(buf.Bytes())
	if !ok || out == "" {
		t.Fatalf("expected ok=true and non-empty output, got ok=%v out=%q", ok, out)
	}

	dec, err := base64.StdEncoding.DecodeString(out)
	if err != nil {
		t.Fatalf("base64 decode: %v", err)
	}

	// JPEG magic.
	if len(dec) < 4 || dec[0] != 0xFF || dec[1] != 0xD8 {
		t.Errorf("expected JPEG SOI marker, got % x", dec[:4])
	}

	img, _, err := image.Decode(bytes.NewReader(dec))
	if err != nil {
		t.Fatalf("decode jpeg: %v", err)
	}
	b := img.Bounds()
	if b.Dx() > 192 {
		t.Errorf("expected width<=192, got %d", b.Dx())
	}
	// Height must be roughly half the width given the 2:1 source aspect.
	if b.Dy() < 50 || b.Dy() > 200 {
		t.Errorf("unexpected thumbnail height: %d", b.Dy())
	}
}

func TestImageInvalidReturnsFalse(t *testing.T) {
	if _, ok := preview.Image([]byte("not an image")); ok {
		t.Errorf("expected ok=false for garbage input")
	}
	if _, ok := preview.Image(nil); ok {
		t.Errorf("expected ok=false for nil input")
	}
}

func TestImageSmallStaysSmall(t *testing.T) {
	// A tiny solid-colour image should still encode without panicking.
	src := image.NewRGBA(image.Rect(0, 0, 16, 16))
	for y := 0; y < 16; y++ {
		for x := 0; x < 16; x++ {
			src.Set(x, y, color.RGBA{R: 200, G: 50, B: 50, A: 255})
		}
	}
	var buf bytes.Buffer
	if err := png.Encode(&buf, src); err != nil {
		t.Fatalf("encode: %v", err)
	}
	out, ok := preview.Image(buf.Bytes())
	if !ok {
		t.Fatalf("expected ok=true")
	}
	if !strings.HasPrefix(out, "/9j/") && !strings.HasPrefix(out, "/9k/") {
		// "/9j/" is the standard base64 prefix of any JPEG starting with FFD8FF.
		t.Logf("note: unusual base64 prefix: %s...", out[:8])
	}
}
