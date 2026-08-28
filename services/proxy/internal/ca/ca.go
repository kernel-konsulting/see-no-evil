// Package ca manages the see-no-evil MITM certificate authority.
//
// Two modes:
//   - auto: generate a self-signed CA at first start. When the
//     PROXY_CA_PASSPHRASE environment variable is set, the private key is
//     encrypted at rest with AES-256-GCM (key derived via PBKDF2-HMAC-SHA256)
//     and persisted as <dataDir>/ca.key.enc. Without a passphrase the key is
//     stored as plaintext ca.key (0600) for backwards compatibility — set the
//     passphrase in production.
//   - byo: load an existing cert+key from the paths given in config.
package ca

import (
	"crypto/aes"
	"crypto/cipher"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/binary"
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

// ---------------------------------------------------------------------------
// At-rest key encryption (AES-256-GCM, PBKDF2-HMAC-SHA256 key derivation)
// ---------------------------------------------------------------------------

const (
	encSaltLen  = 16
	encNonceLen = 12
	encIters    = 210_000
	encKeyLen   = 32
)

// caPassphrase returns PROXY_CA_PASSPHRASE ("" when unset).
func caPassphrase() string {
	return os.Getenv("PROXY_CA_PASSPHRASE")
}

// pbkdf2SHA256 implements PBKDF2-HMAC-SHA256 (stdlib has no PBKDF2; this
// avoids pulling in golang.org/x/crypto for one derivation).
func pbkdf2SHA256(password string, salt []byte, iter, keyLen int) []byte {
	prf := hmac.New(sha256.New, []byte(password))
	hLen := prf.Size()
	numBlocks := (keyLen + hLen - 1) / hLen
	dk := make([]byte, 0, numBlocks*hLen)
	var u, t []byte
	for block := 1; block <= numBlocks; block++ {
		prf.Reset()
		prf.Write(salt)
		var b [4]byte
		binary.BigEndian.PutUint32(b[:], uint32(block))
		prf.Write(b[:])
		u = prf.Sum(nil)
		t = append(t[:0], u...)
		for i := 2; i <= iter; i++ {
			prf.Reset()
			prf.Write(u)
			u = prf.Sum(nil)
			for j := range t {
				t[j] ^= u[j]
			}
		}
		dk = append(dk, t...)
	}
	return dk[:keyLen]
}

// encryptKeyPEM encrypts a PEM-encoded private key. Output layout:
// salt || nonce || ciphertext.
func encryptKeyPEM(passphrase string, keyPEM []byte) ([]byte, error) {
	salt := make([]byte, encSaltLen)
	if _, err := rand.Read(salt); err != nil {
		return nil, fmt.Errorf("salt: %w", err)
	}
	nonce := make([]byte, encNonceLen)
	if _, err := rand.Read(nonce); err != nil {
		return nil, fmt.Errorf("nonce: %w", err)
	}
	block, err := aes.NewCipher(pbkdf2SHA256(passphrase, salt, encIters, encKeyLen))
	if err != nil {
		return nil, fmt.Errorf("aes: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("gcm: %w", err)
	}
	ct := gcm.Seal(nil, nonce, keyPEM, nil)
	out := make([]byte, 0, encSaltLen+encNonceLen+len(ct))
	out = append(out, salt...)
	out = append(out, nonce...)
	out = append(out, ct...)
	return out, nil
}

func decryptKeyPEM(passphrase string, blob []byte) ([]byte, error) {
	if len(blob) < encSaltLen+encNonceLen {
		return nil, errors.New("encrypted key blob too short")
	}
	salt := blob[:encSaltLen]
	nonce := blob[encSaltLen : encSaltLen+encNonceLen]
	ct := blob[encSaltLen+encNonceLen:]
	block, err := aes.NewCipher(pbkdf2SHA256(passphrase, salt, encIters, encKeyLen))
	if err != nil {
		return nil, fmt.Errorf("aes: %w", err)
	}
	gcm, err := cipher.NewGCM(block)
	if err != nil {
		return nil, fmt.Errorf("gcm: %w", err)
	}
	plain, err := gcm.Open(nil, nonce, ct, nil)
	if err != nil {
		return nil, fmt.Errorf("decrypt CA key (wrong PROXY_CA_PASSPHRASE?): %w", err)
	}
	return plain, nil
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
	encKeyPath := filepath.Join(dataDir, "ca.key.enc")
	keyPath := filepath.Join(dataDir, "ca.key")

	if _, err := os.Stat(certPath); err == nil {
		slog.Info("loading existing CA from disk", "path", certPath)
		certPEM, err2 := os.ReadFile(certPath) // #nosec G304
		if err2 != nil {
			return nil, fmt.Errorf("read existing CA cert: %w", err2)
		}
		if _, err3 := os.Stat(encKeyPath); err3 == nil {
			pass := caPassphrase()
			if pass == "" {
				return nil, errors.New("CA key is encrypted (ca.key.enc) but PROXY_CA_PASSPHRASE is not set")
			}
			blob, err4 := os.ReadFile(encKeyPath) // #nosec G304
			if err4 != nil {
				return nil, fmt.Errorf("read encrypted CA key: %w", err4)
			}
			keyPEM, err5 := decryptKeyPEM(pass, blob)
			if err5 != nil {
				return nil, err5
			}
			return parsePEMPair(certPEM, keyPEM)
		}
		keyPEM, err3 := os.ReadFile(keyPath) // #nosec G304
		if err3 != nil {
			return nil, fmt.Errorf("read existing CA key: %w", err3)
		}
		return parsePEMPair(certPEM, keyPEM)
	}

	slog.Info("generating new MITM CA", "dir", dataDir)
	return generateAndSave(dataDir, certPath, encKeyPath, keyPath)
}

func generateAndSave(dataDir, certPath, encKeyPath, keyPath string) (*KeyPair, error) {
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

	// Write the key owner-only, encrypted at rest when a passphrase is set.
	if pass := caPassphrase(); pass != "" {
		blob, err := encryptKeyPEM(pass, keyPEM)
		if err != nil {
			return nil, fmt.Errorf("encrypt CA key: %w", err)
		}
		if err := os.WriteFile(encKeyPath, blob, 0o600); err != nil {
			return nil, fmt.Errorf("write encrypted CA key: %w", err)
		}
		slog.Info("CA key encrypted at rest (AES-256-GCM)", "path", encKeyPath)
	} else {
		if err := os.WriteFile(keyPath, keyPEM, 0o600); err != nil {
			return nil, fmt.Errorf("write CA key: %w", err)
		}
		slog.Warn("PROXY_CA_PASSPHRASE not set; CA key stored in plaintext — set it in production")
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

// leafCacheMaxEntries bounds the per-host leaf cache. Every unique CONNECT
// hostname mints a cached leaf *before* the TLS handshake, so an unbounded
// map would let a LAN client exhaust proxy memory by spraying hostnames.
var leafCacheMaxEntries = 4096

type leafEntry struct {
	cfg      *tls.Config
	lastUsed time.Time
}

// LeafCache mints and caches per-host TLS configurations signed by the CA.
// It is safe for concurrent use and evicts the least-recently-used entry once
// the cache reaches leafCacheMaxEntries.
type LeafCache struct {
	ca *KeyPair
	mu sync.Mutex
	m  map[string]leafEntry
}

func NewLeafCache(ca *KeyPair) *LeafCache {
	return &LeafCache{ca: ca, m: make(map[string]leafEntry)}
}

// TLSConfig returns a *tls.Config presenting a leaf certificate for the given
// SNI host, minting one on first use.
func (lc *LeafCache) TLSConfig(host string) *tls.Config {
	lc.mu.Lock()
	defer lc.mu.Unlock()

	if e, ok := lc.m[host]; ok {
		e.lastUsed = time.Now()
		lc.m[host] = e
		return e.cfg
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
	if len(lc.m) >= leafCacheMaxEntries {
		lc.evictOldestLocked()
	}
	lc.m[host] = leafEntry{cfg: cfg, lastUsed: time.Now()}
	return cfg
}

// evictOldestLocked removes the least-recently-used entry. Caller holds mu.
func (lc *LeafCache) evictOldestLocked() {
	var oldestHost string
	var oldest time.Time
	for h, e := range lc.m {
		if oldestHost == "" || e.lastUsed.Before(oldest) {
			oldestHost = h
			oldest = e.lastUsed
		}
	}
	if oldestHost != "" {
		delete(lc.m, oldestHost)
	}
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
