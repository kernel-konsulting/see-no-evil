// Package policy provides an HTTP client for the see-no-evil policy API
// (/v1/decide endpoint).
package policy

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"time"
)

// Client calls the policy API's /v1/decide endpoint.
type Client struct {
	baseURL    string
	httpClient *http.Client
}

func NewClient(baseURL string) *Client {
	return &Client{
		baseURL: baseURL,
		httpClient: &http.Client{
			Timeout: 5 * time.Second,
		},
	}
}

// DecideRequest mirrors the API's DecideRequest schema.
type DecideRequest struct {
	URL              string             `json:"url"`
	DeviceMAC        string             `json:"device_mac,omitempty"`
	ContentType      string             `json:"content_type,omitempty"`
	ClassifierScores map[string]float32 `json:"classifier_scores,omitempty"`
	// ThumbnailB64 is an optional blurred preview the API persists with the
	// quarantine entry when the decision is "block".
	ThumbnailB64 string `json:"thumbnail_b64,omitempty"`
}

// DecideResponse mirrors the API's DecideResponse schema.
type DecideResponse struct {
	Decision string `json:"decision"` // "allow" | "block" | "warn"
	Reason   string `json:"reason"`
}

// Decide calls /v1/decide and returns the policy decision.
func (c *Client) Decide(ctx context.Context, req DecideRequest) (*DecideResponse, error) {
	body, err := json.Marshal(req)
	if err != nil {
		return nil, fmt.Errorf("marshal decide request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+"/v1/decide", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("build decide request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := c.httpClient.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("decide HTTP: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("decide API returned %d", resp.StatusCode)
	}

	var result DecideResponse
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("decode decide response: %w", err)
	}
	return &result, nil
}
