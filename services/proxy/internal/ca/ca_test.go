package ca_test

import (
	"crypto/tls"
	"crypto/x509"
	"net"
	"os"
	"path/filepath"
	"testing"

	"github.com/kernel-konsulting/see-no-evil/services/proxy/internal/ca"
)

func TestGenerateCA(t *testing.T) {
	dir := t.TempDir()
	kp, err := ca.LoadFromFields("auto", "", "", dir)
	if err != nil {
		t.Fatalf("generate CA: %v", err)
	}
	if kp.Cert == nil || kp.Key == nil {
		t.Fatal("key pair incomplete")
	}
	if !kp.Cert.IsCA {
		t.Error("expected IsCA=true")
	}
	// Cert file should exist.
	if _, err := os.Stat(filepath.Join(dir, "ca.crt")); err != nil {
		t.Errorf("ca.crt not written: %v", err)
	}
}

func TestLoadExistingCA(t *testing.T) {
	dir := t.TempDir()
	// Generate once.
	kp1, err := ca.LoadFromFields("auto", "", "", dir)
	if err != nil {
		t.Fatalf("generate: %v", err)
	}
	// Load again — must match.
	kp2, err := ca.LoadFromFields("auto", "", "", dir)
	if err != nil {
		t.Fatalf("reload: %v", err)
	}
	if kp1.Cert.SerialNumber.Cmp(kp2.Cert.SerialNumber) != 0 {
		t.Error("serial numbers differ after reload")
	}
}

func TestLeafCacheMintsValidCert(t *testing.T) {
	dir := t.TempDir()
	kp, err := ca.LoadFromFields("auto", "", "", dir)
	if err != nil {
		t.Fatalf("generate CA: %v", err)
	}

	lc := ca.NewLeafCache(kp)
	tlsCfg := lc.TLSConfig("example.com")

	if len(tlsCfg.Certificates) == 0 {
		t.Fatal("no certificates in TLS config")
	}

	leafTLS := tlsCfg.Certificates[0]
	leaf, err := x509.ParseCertificate(leafTLS.Certificate[0])
	if err != nil {
		t.Fatalf("parse leaf cert: %v", err)
	}

	pool := x509.NewCertPool()
	pool.AddCert(kp.Cert)

	_, err = leaf.Verify(x509.VerifyOptions{
		DNSName: "example.com",
		Roots:   pool,
		KeyUsages: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	})
	if err != nil {
		t.Errorf("leaf cert does not verify against CA: %v", err)
	}
}

func TestLeafCacheSameHostReturnsSameCert(t *testing.T) {
	dir := t.TempDir()
	kp, _ := ca.LoadFromFields("auto", "", "", dir)
	lc := ca.NewLeafCache(kp)

	cfg1 := lc.TLSConfig("host.example.com")
	cfg2 := lc.TLSConfig("host.example.com")

	// Pointer equality: same config object means same cached entry.
	if cfg1 != cfg2 {
		t.Error("expected same *tls.Config for the same host (cache miss)")
	}
}

func TestLeafCacheIPAddress(t *testing.T) {
	dir := t.TempDir()
	kp, _ := ca.LoadFromFields("auto", "", "", dir)
	lc := ca.NewLeafCache(kp)

	tlsCfg := lc.TLSConfig("192.168.1.1:443")
	leaf, _ := x509.ParseCertificate(tlsCfg.Certificates[0].Certificate[0])

	found := false
	for _, ip := range leaf.IPAddresses {
		if ip.Equal(net.ParseIP("192.168.1.1")) {
			found = true
		}
	}
	if !found {
		t.Error("IP SAN not set for IP address host")
	}
	_ = tls.Certificate{} // ensure tls import used
}
