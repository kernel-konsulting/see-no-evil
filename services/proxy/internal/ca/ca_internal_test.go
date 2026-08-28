package ca

import (
	"crypto/tls"
	"os"
	"path/filepath"
	"testing"
)

// TestEncryptDecryptKeyPEMRoundTrip checks that a PEM key survives an
// encrypt→decrypt cycle with the right passphrase.
func TestEncryptDecryptKeyPEMRoundTrip(t *testing.T) {
	keyPEM := []byte("-----BEGIN EC PRIVATE KEY-----\nZmFrZQ==\n-----END EC PRIVATE KEY-----\n")
	blob, err := encryptKeyPEM("hunter22", keyPEM)
	if err != nil {
		t.Fatalf("encrypt: %v", err)
	}
	// salt || nonce || ciphertext(+GCM tag)
	if len(blob) != encSaltLen+encNonceLen+len(keyPEM)+16 {
		t.Fatalf("blob length = %d, want %d", len(blob), encSaltLen+encNonceLen+len(keyPEM)+16)
	}
	got, err := decryptKeyPEM("hunter22", blob)
	if err != nil {
		t.Fatalf("decrypt: %v", err)
	}
	if string(got) != string(keyPEM) {
		t.Fatal("round-trip mismatch")
	}
}

// TestDecryptKeyPEMWrongPassphrase ensures a wrong passphrase fails loudly.
func TestDecryptKeyPEMWrongPassphrase(t *testing.T) {
	blob, err := encryptKeyPEM("right", []byte("secret"))
	if err != nil {
		t.Fatalf("encrypt: %v", err)
	}
	if _, err := decryptKeyPEM("wrong", blob); err == nil {
		t.Fatal("expected error with wrong passphrase")
	}
}

// TestCAEncryptedAtRest checks that with PROXY_CA_PASSPHRASE set, generation
// writes ca.key.enc (and no plaintext ca.key), and that loading requires the
// passphrase.
func TestCAEncryptedAtRest(t *testing.T) {
	t.Setenv("PROXY_CA_PASSPHRASE", "s3cret!")
	dir := t.TempDir()

	if _, err := LoadFromFields("auto", "", "", dir); err != nil {
		t.Fatalf("generate encrypted CA: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "ca.key.enc")); err != nil {
		t.Fatalf("ca.key.enc not written: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "ca.key")); err == nil {
		t.Fatal("plaintext ca.key must not be written when a passphrase is set")
	}
	plain, err := os.ReadFile(filepath.Join(dir, "ca.key.enc"))
	if err != nil {
		t.Fatalf("read blob: %v", err)
	}
	if len(plain) == 0 || string(plain) == "-----BEGIN EC PRIVATE KEY-----" {
		t.Fatal("key blob appears to be plaintext PEM")
	}

	// Reload with the passphrase must succeed.
	if _, err := LoadFromFields("auto", "", "", dir); err != nil {
		t.Fatalf("reload encrypted CA with passphrase: %v", err)
	}
}

// TestCAEncryptedLoadRequiresPassphrase checks that loading an encrypted key
// without the passphrase fails rather than silently falling back.
func TestCAEncryptedLoadRequiresPassphrase(t *testing.T) {
	t.Setenv("PROXY_CA_PASSPHRASE", "s3cret!")
	dir := t.TempDir()
	if _, err := LoadFromFields("auto", "", "", dir); err != nil {
		t.Fatalf("generate: %v", err)
	}
	t.Setenv("PROXY_CA_PASSPHRASE", "")
	if _, err := LoadFromFields("auto", "", "", dir); err == nil {
		t.Fatal("expected error loading encrypted CA without passphrase")
	}
}

// TestLeafCacheEvictsOldest verifies the bounded cache evicts the
// least-recently-used entry once the cap is reached.
func TestLeafCacheEvictsOldest(t *testing.T) {
	old := leafCacheMaxEntries
	leafCacheMaxEntries = 2
	defer func() { leafCacheMaxEntries = old }()

	dir := t.TempDir()
	kp, err := LoadFromFields("auto", "", "", dir)
	if err != nil {
		t.Fatalf("generate CA: %v", err)
	}
	lc := NewLeafCache(kp)

	// First host is the least-recently-used once two more are minted.
	lc.TLSConfig("a.example.com")
	lc.TLSConfig("b.example.com")
	lc.TLSConfig("c.example.com") // evicts a

	if _, ok := lc.m["a.example.com"]; ok {
		t.Error("expected a.example.com to be evicted")
	}
	if _, ok := lc.m["b.example.com"]; !ok {
		t.Error("expected b.example.com to remain")
	}
	if _, ok := lc.m["c.example.com"]; !ok {
		t.Error("expected c.example.com to remain")
	}

	// Touching b makes it recent; the next mint evicts c.
	_ = lc.TLSConfig("b.example.com")
	lc.TLSConfig("d.example.com")
	if _, ok := lc.m["c.example.com"]; ok {
		t.Error("expected c.example.com to be evicted after b was touched")
	}
}

// TestLeafCacheBoundedAfterChurn mints far more hosts than the cap and checks
// the map stays within bounds.
func TestLeafCacheBoundedAfterChurn(t *testing.T) {
	old := leafCacheMaxEntries
	leafCacheMaxEntries = 16
	defer func() { leafCacheMaxEntries = old }()

	dir := t.TempDir()
	kp, err := LoadFromFields("auto", "", "", dir)
	if err != nil {
		t.Fatalf("generate CA: %v", err)
	}
	lc := NewLeafCache(kp)
	for i := 0; i < 200; i++ {
		host := string(rune('a'+i%26)) + string(rune('a'+(i/26)%26)) + ".example.com"
		_ = lc.TLSConfig(host)
	}
	if len(lc.m) > leafCacheMaxEntries {
		t.Fatalf("cache grew to %d entries, cap is %d", len(lc.m), leafCacheMaxEntries)
	}
	_ = tls.Certificate{} // keep tls import for parity with existing tests
}
