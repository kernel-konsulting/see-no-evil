// Package config loads and validates the proxy's slice of config.yaml.
package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"

	"gopkg.in/yaml.v3"
)

// Root mirrors the fields from config.yaml that the proxy needs.
// Unknown fields are ignored; the full config is also read by other services.
type Root struct {
	Pod struct {
		Hostname string `yaml:"hostname"`
		DataDir  string `yaml:"data_dir"`
	} `yaml:"pod"`

	API struct {
		// Internal address of the api service (populated by compose).
		InternalAddr string `yaml:"internal_addr"`
	} `yaml:"api"`

	Proxy ProxyConfig `yaml:"proxy"`

	Classifiers ClassifiersConfig `yaml:"classifiers"`
}

type ProxyConfig struct {
	BindHTTP  string `yaml:"bind_http"`
	BindHTTPS string `yaml:"bind_https"`
	// MetricsAddr is not in the user-facing config; set via env or default.
	MetricsAddr string `yaml:"-"`

	CA struct {
		Mode     string `yaml:"mode"`      // "auto" | "byo"
		CertPath string `yaml:"cert_path"` // used when mode=byo
		KeyPath  string `yaml:"key_path"`
		DataDir  string `yaml:"-"` // injected after load
	} `yaml:"ca"`

	BypassDomains []string `yaml:"bypass_domains"`

	SafeSearch struct {
		Google            bool `yaml:"google"`
		Bing              bool `yaml:"bing"`
		DuckDuckGo        bool `yaml:"ddg"`
		YouTubeRestricted bool `yaml:"youtube_restricted"`
	} `yaml:"safesearch"`

	MaxInspectBody string `yaml:"max_inspect_body"` // legacy fallback (e.g. "10MiB")

	// Per-content-type inspection caps. Empty / 0 / "unlimited" means no
	// truncation — the entire body is fed to the classifier. Images and text
	// MUST be sent in full so classifiers can decode them; truncating an
	// image past the header makes Pillow / ONNX reject the bytes outright.
	// Video has a soft default cap because the sampler buffers to disk before
	// extracting frames.
	MaxImageBody string `yaml:"max_image_body"` // default: unlimited
	MaxTextBody  string `yaml:"max_text_body"`  // default: unlimited
	MaxVideoBody string `yaml:"max_video_body"` // default: 500MiB

	TextInspection TextInspectionConfig `yaml:"text_inspection"`

	// APIToken authenticates proxy→API calls (/v1/decide, /v1/runtime,
	// /v1/quota/heartbeat). Mirrored from the api service's
	// SEENOEVIL_PROXY_TOKEN. Empty disables the header (tests, single-host).
	APIToken string `yaml:"api_token"`

	// FailClosed makes classifier / policy failures block instead of
	// allowing through. The default (false) is fail-open: an unhealthy
	// classifier or API degrades filtering but does not break the network.
	FailClosed bool `yaml:"fail_closed"`
}

// TextInspectionConfig controls how the proxy reacts when the text classifier
// flags response content.
//
// Modes:
//   - "off":   skip text classification entirely.
//   - "block": block the whole page (legacy default).
//   - "strip": rewrite the body, replacing flagged paragraphs with a redaction
//     marker, but still serve it to the client.
type TextInspectionConfig struct {
	Mode          string  `yaml:"mode"`           // off | block | strip
	NSFWThreshold float32 `yaml:"nsfw_threshold"` // default 0.5
	Redaction     string  `yaml:"redaction"`      // text used by strip mode
}

// MaxInspectBytes converts the IEC size string to bytes.
//
// Returns the legacy global cap. Prefer the per-type accessors below.
func (p ProxyConfig) MaxInspectBytes() int64 {
	return parseSize(p.MaxInspectBody, 10<<20) // default 10 MiB
}

// unlimited is the sentinel returned for "send everything" — large enough
// that io.LimitReader effectively reads to EOF, small enough to fit in int64.
const unlimited int64 = 1 << 62

func parseLimit(s string, fallback int64) int64 {
	trim := strings.ToLower(strings.TrimSpace(s))
	switch trim {
	case "":
		return fallback
	case "0", "unlimited", "none", "-1", "full":
		return unlimited
	}
	return parseSize(s, fallback)
}

// MaxImageBytes returns the cap for image bodies. Default = unlimited so the
// classifier always sees a complete, decodable image.
func (p ProxyConfig) MaxImageBytes() int64 {
	return parseLimit(p.MaxImageBody, unlimited)
}

// MaxTextBytes returns the cap for HTML/JSON/text bodies. Default = unlimited.
func (p ProxyConfig) MaxTextBytes() int64 {
	return parseLimit(p.MaxTextBody, unlimited)
}

// MaxVideoBytes returns the cap for buffered video bodies. Default = 500 MiB
// (the sampler still streams frames evenly across whatever was buffered).
func (p ProxyConfig) MaxVideoBytes() int64 {
	return parseLimit(p.MaxVideoBody, 500<<20)
}

type ClassifiersConfig struct {
	Image struct {
		Addr string `yaml:"addr"` // e.g. "image-classifier:50051"
	} `yaml:"image"`
	Text struct {
		Addr string `yaml:"addr"`
	} `yaml:"text"`
	Video struct {
		Addr string `yaml:"addr"` // empty disables video sampling
	} `yaml:"video"`
}

// Load reads and parses config.yaml.  If path is empty it tries
// CONFIG_PATH env var, then /data/config.yaml, then ./config.yaml.
func Load(path string) (*Root, error) {
	if path == "" {
		path = os.Getenv("CONFIG_PATH")
	}
	if path == "" {
		for _, candidate := range []string{"/data/config.yaml", "./config.yaml"} {
			if _, err := os.Stat(candidate); err == nil {
				path = candidate
				break
			}
		}
	}
	if path == "" {
		return nil, fmt.Errorf("no config file found; set CONFIG_PATH or place config.yaml in /data")
	}

	data, err := os.ReadFile(path) // #nosec G304 — intentional config file read
	if err != nil {
		return nil, fmt.Errorf("read config: %w", err)
	}

	var root Root
	if err := yaml.Unmarshal(data, &root); err != nil {
		return nil, fmt.Errorf("parse config: %w", err)
	}

	root.setDefaults()
	return &root, nil
}

func (r *Root) setDefaults() {
	if r.Proxy.BindHTTP == "" {
		r.Proxy.BindHTTP = "0.0.0.0:8080"
	}
	if r.Proxy.BindHTTPS == "" {
		r.Proxy.BindHTTPS = "0.0.0.0:8443"
	}
	if r.Proxy.MetricsAddr == "" {
		r.Proxy.MetricsAddr = envOr("METRICS_ADDR", "0.0.0.0:9100")
	}
	if r.Proxy.CA.DataDir == "" {
		d := r.Pod.DataDir
		if d == "" {
			d = "/data"
		}
		r.Proxy.CA.DataDir = d + "/ca"
	}
	if r.Classifiers.Image.Addr == "" {
		r.Classifiers.Image.Addr = envOr("IMAGE_CLASSIFIER_ADDR", "image-classifier:50051")
	}
	if r.Classifiers.Text.Addr == "" {
		r.Classifiers.Text.Addr = envOr("TEXT_CLASSIFIER_ADDR", "text-classifier:50052")
	}
	if r.Classifiers.Video.Addr == "" {
		r.Classifiers.Video.Addr = envOr("VIDEO_SAMPLER_ADDR", "video-sampler:50053")
	}
	if r.API.InternalAddr == "" {
		r.API.InternalAddr = envOr("API_ADDR", "api:8000")
	}
	if r.Proxy.TextInspection.Mode == "" {
		r.Proxy.TextInspection.Mode = strings.ToLower(envOr("TEXT_INSPECTION_MODE", "block"))
	} else {
		r.Proxy.TextInspection.Mode = strings.ToLower(r.Proxy.TextInspection.Mode)
	}
	if r.Proxy.TextInspection.NSFWThreshold == 0 {
		r.Proxy.TextInspection.NSFWThreshold = envFloat("TEXT_NSFW_THRESHOLD", 0.5)
	}
	if r.Proxy.TextInspection.Redaction == "" {
		r.Proxy.TextInspection.Redaction = envOr("TEXT_REDACTION", "[content removed by see-no-evil]")
	}
	if r.Proxy.APIToken == "" {
		r.Proxy.APIToken = os.Getenv("SEENOEVIL_PROXY_TOKEN")
	}
}

func envFloat(key string, fallback float32) float32 {
	v := os.Getenv(key)
	if v == "" {
		return fallback
	}
	var f float32
	if _, err := fmt.Sscan(v, &f); err != nil {
		return fallback
	}
	return f
}

// PolicyAPIURL returns the base URL for the policy/decide HTTP endpoint.
func (r *Root) PolicyAPIURL() string {
	addr := r.API.InternalAddr
	if !strings.HasPrefix(addr, "http") {
		addr = "http://" + addr
	}
	return addr
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// parseSize converts IEC size strings (10MiB, 2GiB) to bytes.
//
// Order matters: we must test the longest suffixes first because every IEC
// suffix ends in "B" — iterating a map (random order) was matching "B" on
// strings like "10MIB" and silently returning 10 bytes.
func parseSize(s string, fallback int64) int64 {
	if s == "" {
		return fallback
	}
	type unit struct {
		suffix string
		mult   int64
	}
	units := []unit{
		{"GIB", 1 << 30},
		{"MIB", 1 << 20},
		{"KIB", 1 << 10},
		{"B", 1},
	}
	s = strings.TrimSpace(strings.ToUpper(s))
	for _, u := range units {
		if !strings.HasSuffix(s, u.suffix) {
			continue
		}
		num := strings.TrimSpace(strings.TrimSuffix(s, u.suffix))
		n, err := strconv.ParseInt(num, 10, 64)
		if err != nil {
			return fallback
		}
		return n * u.mult
	}
	// No suffix — try a bare integer (interpreted as bytes).
	if n, err := strconv.ParseInt(s, 10, 64); err == nil {
		return n
	}
	return fallback
}
