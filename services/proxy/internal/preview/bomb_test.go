package preview_test

import (
	"bytes"
	"encoding/binary"
	"hash/crc32"
	"image"
	"image/png"
	"testing"

	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/preview"
)

// bombPNG builds a structurally valid PNG whose IHDR declares huge
// dimensions but carries no pixel data. image.DecodeConfig succeeds on it;
// image.Decode would attempt a giant allocation — which is exactly what the
// pixel budget must prevent.
func bombPNG(width, height uint32) []byte {
	var b bytes.Buffer
	b.Write([]byte{0x89, 'P', 'N', 'G', '\r', '\n', 0x1a, '\n'})
	chunk := func(typ string, data []byte) {
		var hdr [8]byte
		binary.BigEndian.PutUint32(hdr[:4], uint32(len(data)))
		copy(hdr[4:], typ)
		b.Write(hdr[:])
		b.Write(data)
		var crc [4]byte
		binary.BigEndian.PutUint32(crc[:], crc32.ChecksumIEEE(append([]byte(typ), data...)))
		b.Write(crc[:])
	}
	ihdr := make([]byte, 13)
	binary.BigEndian.PutUint32(ihdr[0:4], width)
	binary.BigEndian.PutUint32(ihdr[4:8], height)
	ihdr[8] = 8  // bit depth
	ihdr[9] = 2  // color type: truecolor
	ihdr[10] = 0 // compression
	ihdr[11] = 0 // filter
	ihdr[12] = 0 // interlace
	chunk("IHDR", ihdr)
	chunk("IDAT", []byte{0x78, 0x9c, 0x63, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x01}) // minimal
	chunk("IEND", nil)
	return b.Bytes()
}

func TestClearRejectsDecompressionBomb(t *testing.T) {
	// 100000 x 100000 = 10^10 pixels, far above the 50 MP budget.
	raw := bombPNG(100_000, 100_000)
	if _, ok := preview.Clear(raw); ok {
		t.Fatal("Clear() accepted a decompression bomb image")
	}
}

func TestImageRejectsDecompressionBomb(t *testing.T) {
	raw := bombPNG(100_000, 100_000)
	if _, ok := preview.Image(raw); ok {
		t.Fatal("Image() accepted a decompression bomb image")
	}
}

func TestPreviewStillWorksForNormalSizes(t *testing.T) {
	var buf bytes.Buffer
	img := image.NewRGBA(image.Rect(0, 0, 64, 64))
	if err := png.Encode(&buf, img); err != nil {
		t.Fatalf("encode: %v", err)
	}
	if _, ok := preview.Clear(buf.Bytes()); !ok {
		t.Fatal("Clear() rejected a small real image")
	}
	if _, ok := preview.Image(buf.Bytes()); !ok {
		t.Fatal("Image() rejected a small real image")
	}
}
