// Package preview produces small, blurred preview thumbnails for the
// quarantine queue. Operators need *enough* visual context to triage a
// quarantined item without exposing them to the raw blocked content.
//
// We always return a base64-encoded JPEG. Decoding/encoding is best-effort:
// on any failure we return ("", false) so the caller can omit the field.
package preview

import (
	"bytes"
	"encoding/base64"
	"image"
	"image/color"
	"image/jpeg"

	// Side-effect imports register decoders.
	_ "image/gif"
	_ "image/png"

	xdraw "golang.org/x/image/draw"
)

const (
	maxWidth   = 192 // matches the UI quarantine card width
	jpegQ      = 60
	blurPasses = 6 // 3x3 box-blur iterations
)

// Image returns a base64-encoded JPEG thumbnail of img, downscaled and
// box-blurred. Returns ("", false) on any decode/encode failure.
func Image(raw []byte) (string, bool) {
	if len(raw) == 0 {
		return "", false
	}
	src, _, err := image.Decode(bytes.NewReader(raw))
	if err != nil {
		return "", false
	}
	thumb := scale(src, maxWidth)
	blurred := boxBlur(thumb, blurPasses)

	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, blurred, &jpeg.Options{Quality: jpegQ}); err != nil {
		return "", false
	}
	return base64.StdEncoding.EncodeToString(buf.Bytes()), true
}

// scale downscales src so its width <= w (preserving aspect ratio). If src is
// already small enough we still return an *image.RGBA so the blur step has a
// uniform input.
func scale(src image.Image, w int) *image.RGBA {
	b := src.Bounds()
	if b.Dx() <= w {
		out := image.NewRGBA(b)
		xdraw.Copy(out, b.Min, src, b, xdraw.Src, nil)
		return out
	}
	h := int(float64(b.Dy()) * float64(w) / float64(b.Dx()))
	dst := image.NewRGBA(image.Rect(0, 0, w, h))
	xdraw.CatmullRom.Scale(dst, dst.Bounds(), src, b, xdraw.Over, nil)
	return dst
}

// boxBlur applies a 3x3 box blur n times to src and returns a new image.
// Heavy-handed but exactly what we want: the goal is to render the preview
// unrecognisable enough to safely display in the UI.
func boxBlur(src *image.RGBA, passes int) *image.RGBA {
	cur := src
	for i := 0; i < passes; i++ {
		cur = boxBlurOnce(cur)
	}
	return cur
}

func boxBlurOnce(src *image.RGBA) *image.RGBA {
	b := src.Bounds()
	dst := image.NewRGBA(b)
	for y := b.Min.Y; y < b.Max.Y; y++ {
		for x := b.Min.X; x < b.Max.X; x++ {
			var r, g, bl, a, n uint32
			for dy := -1; dy <= 1; dy++ {
				yy := y + dy
				if yy < b.Min.Y || yy >= b.Max.Y {
					continue
				}
				for dx := -1; dx <= 1; dx++ {
					xx := x + dx
					if xx < b.Min.X || xx >= b.Max.X {
						continue
					}
					rr, gg, bb, aa := src.At(xx, yy).RGBA()
					r += rr
					g += gg
					bl += bb
					a += aa
					n++
				}
			}
			if n == 0 {
				dst.Set(x, y, src.At(x, y))
				continue
			}
			dst.SetRGBA(x, y, color.RGBA{
				R: uint8((r / n) >> 8),
				G: uint8((g / n) >> 8),
				B: uint8((bl / n) >> 8),
				A: uint8((a / n) >> 8),
			})
		}
	}
	return dst
}
