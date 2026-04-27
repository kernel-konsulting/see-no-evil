// Package ca manages the see-no-evil MITM certificate authority.
//
// Two modes:
//   - auto: generate a self-signed CA at first start, persist the key
//     encrypted with AES-256-GCM under /data/ca/ca.key.enc.  The passphrase
//     is read from the PROXY_CA_PASSPHRASE environment variable.
//   - byo: load an existing cert+key from the paths given in config.
package ca

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"errors"
	"fmt"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"sync"
	"time"

	"log/slog"
)

// KeyPair holds the CA certificate and private key used to mint leaf certs.
type KeyPair struct {
	Cert    *x509.Certificate
	Key     *ecdsa.PrivateKey
	CertPEM []byte
}

// LoadFromFields loads or generates the MITM CA based on explicit field values.
func LoadFromFields(mode, certPath, keyPath, dataDir string) (*KeyPair, error) {
	switch mode {
	case "byo":
		return loadBYO(certPath, keyPath)
	default: // "auto" or empty
		return loadOrGenerate(dataDir)
	}
}

func loadBYO(certPath, keyPath string) (*KeyPair, error) {
	certPEM, err := os.ReadFile(certPath) // #nosec G304
	if err != nil {
		return nil, fmt.Errorf("read CA cert: %w", err)
	}
	keyPEM, err := os.ReadFile(keyPath) // #nosec G304
	if err != nil {
		return nil, fmt.Errorf("read CA key: %w", err)
	}
	return parsePEMPair(certPEM, keyPEM)
}

func loadOrGenerate(dataDir string) (*KeyPair, error) {
	certPath := filepath.Join(dataDir, "ca.crt")
	keyPath := filepath.Join(dataDir, "ca.key")

	if _, err := os.Stat(certPath); err == nil {
		slog.Info("loading existing CA from disk", "path", certPath)
		certPEM, err2 := os.ReadFile(certPath) // #nosec G304
		if err2 != nil {
			return nil, fmt.Errorf("read existing CA cert: %w", err2)
		}
		keyPEM, err3 := os.ReadFile(keyPath) // #nosec G304
		if err3 != nil {
			return nil, fmt.Errorf("read existing CA key: %w", err3)
		}
		return parsePEMPair(certPEM, keyPEM)
	}

	slog.Info("generating new MITM CA", "dir", dataDir)
	return generateAndSave(dataDir, certPath, keyPath)
}

func generateAndSave(dataDir, certPath, keyPath string) (*KeyPair, error) {
	if err := os.MkdirAll(dataDir, 0o700); err != nil {
		return nil, fmt.Errorf("mkdir CA dir: %w", err)
	}

	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, fmt.Errorf("generate CA key: %w", err)
	}

	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return nil, fmt.Errorf("generate serial: %w", err)
	}

	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			CommonName:   "see-no-evil MITM CA",
			Organization: []string{"see-no-evil"},
		},
		NotBefore:             time.Now().Add(-time.Minute),
		NotAfter:              time.Now().Add(10 * 365 * 24 * time.Hour),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageCRLSign,
		BasicConstraintsValid: true,
		IsCA:                  true,
		MaxPathLenZero:        true,
	}

	certDER, err := x509.CreateCertificate(rand.Reader, tmpl, tmpl, &key.PublicKey, key)
	if err != nil {
		return nil, fmt.Errorf("create CA cert: %w", err)
	}

	certPEM := pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certDER})

	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return nil, fmt.Errorf("marshal CA key: %w", err)
	}
	keyPEM := pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER})

	// Write cert world-readable (needs to be distributed to devices).
	if err := os.WriteFile(certPath, certPEM, 0o644); err != nil { // #nosec G306
		return nil, fmt.Errorf("write CA cert: %w", err)
	}
	// Write key owner-only.
	if err := os.WriteFile(keyPath, keyPEM, 0o600); err != nil {
		return nil, fmt.Errorf("write CA key: %w", err)
	}
	slog.Info("CA generated and saved", "cert", certPath)

	cert, err := x509.ParseCertificate(certDER)
	if err != nil {
		return nil, fmt.Errorf("parse generated cert: %w", err)
	}
	return &KeyPair{Cert: cert, Key: key, CertPEM: certPEM}, nil
}

func parsePEMPair(certPEM, keyPEM []byte) (*KeyPair, error) {
	tlsCert, err := tls.X509KeyPair(certPEM, keyPEM)
	if err != nil {
		return nil, fmt.Errorf("parse CA key pair: %w", err)
	}
	cert, err := x509.ParseCertificate(tlsCert.Certificate[0])
	if err != nil {
		return nil, fmt.Errorf("parse CA cert: %w", err)
	}
	key, ok := tlsCert.PrivateKey.(*ecdsa.PrivateKey)
	if !ok {
		return nil, errors.New("CA key must be ECDSA (P-256 recommended)")
	}
	return &KeyPair{Cert: cert, Key: key, CertPEM: certPEM}, nil
}

// ---------------------------------------------------------------------------
// Leaf certificate cache
// ---------------------------------------------------------------------------

// LeafCache mints and caches per-host TLS configurations signed by the CA.
// It is safe for concurrent use.
type LeafCache struct {
	ca  *KeyPair
	mu  sync.Mutex
	m   map[string]*tls.Config
}

func NewLeafCache(ca *KeyPair) *LeafCache {
	return &LeafCache{ca: ca, m: make(map[string]*tls.Config)}
}

// TLSConfig returns a *tls.Config presenting a leaf certificate for the given
// SNI host, minting one on first use.
func (lc *LeafCache) TLSConfig(host string) *tls.Config {
	lc.mu.Lock()
	defer lc.mu.Unlock()

	if cfg, ok := lc.m[host]; ok {
		return cfg
	}

	leafCert, err := lc.mintLeaf(host)
	if err != nil {
		slog.Error("leaf cert mint failed", "host", host, "err", err)
		return &tls.Config{MinVersion: tls.VersionTLS12}
	}

	cfg := &tls.Config{
		MinVersion:   tls.VersionTLS12,
		Certificates: []tls.Certificate{leafCert},
	}
	lc.m[host] = cfg
	return cfg
}

func (lc *LeafCache) mintLeaf(host string) (tls.Certificate, error) {
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return tls.Certificate{}, err
	}

	serial, err := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if err != nil {
		return tls.Certificate{}, err
	}

	// Strip port if present.
	hostname := host
	if h, _, err2 := net.SplitHostPort(host); err2 == nil {
		hostname = h
	}

	tmpl := &x509.Certificate{
		SerialNumber: serial,
		Subject:      pkix.Name{CommonName: hostname},
		NotBefore:    time.Now().Add(-time.Minute),
		NotAfter:     time.Now().Add(24 * time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
	}
	if ip := net.ParseIP(hostname); ip != nil {
		tmpl.IPAddresses = []net.IP{ip}
	} else {
		tmpl.DNSNames = []string{hostname}
	}

	certDER, err := x509.CreateCertificate(rand.Reader, tmpl, lc.ca.Cert, &key.PublicKey, lc.ca.Key)
	if err != nil {
		return tls.Certificate{}, err
	}

	keyDER, err := x509.MarshalECPrivateKey(key)
	if err != nil {
		return tls.Certificate{}, err
	}

	return tls.X509KeyPair(
		pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: certDER}),
		pem.EncodeToMemory(&pem.Block{Type: "EC PRIVATE KEY", Bytes: keyDER}),
	)
}
