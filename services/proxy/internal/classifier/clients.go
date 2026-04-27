// Package classifier provides gRPC client wrappers for the image and text
// classifier services.
package classifier

import (
	"context"
	"fmt"
	"time"

	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"

	classifyv1 "github.com/kernel-konsulting/see-no-evil/services/proxy/gen/classify/v1"
)

// Clients holds the gRPC stubs for all classifier services.
type Clients struct {
	Image classifyv1.ImageClassifierClient
	Text  classifyv1.TextClassifierClient

	conns []*grpc.ClientConn
}

// NewClientsFromAddrs dials the classifier gRPC services at the given addresses.
func NewClientsFromAddrs(imageAddr, textAddr string) (*Clients, error) {
	imgConn, err := dial(imageAddr)
	if err != nil {
		return nil, fmt.Errorf("dial image-classifier at %s: %w", imageAddr, err)
	}
	txtConn, err := dial(textAddr)
	if err != nil {
		imgConn.Close()
		return nil, fmt.Errorf("dial text-classifier at %s: %w", textAddr, err)
	}
	return &Clients{
		Image: classifyv1.NewImageClassifierClient(imgConn),
		Text:  classifyv1.NewTextClassifierClient(txtConn),
		conns: []*grpc.ClientConn{imgConn, txtConn},
	}, nil
}

func dial(addr string) (*grpc.ClientConn, error) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	//nolint:staticcheck // grpc.DialContext deprecated but replacement (NewClient) is not in all versions
	return grpc.DialContext(ctx, addr,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
		grpc.WithBlock(),
	)
}

// Close shuts down all underlying gRPC connections.
func (c *Clients) Close() {
	for _, conn := range c.conns {
		_ = conn.Close()
	}
}

// ClassifyImageResult is a simplified result from the image classifier.
type ClassifyImageResult struct {
	Scores    map[string]float32
	Action    classifyv1.Action
	Reason    string
	LatencyMs int64
}

// ClassifyImage calls the image-classifier service with the given bytes.
func (c *Clients) ClassifyImage(ctx context.Context, imageData []byte, requestID string) (*ClassifyImageResult, error) {
	resp, err := c.Image.Classify(ctx, &classifyv1.ClassifyImageRequest{
		ImageData: imageData,
		RequestId: requestID,
	})
	if err != nil {
		return nil, err
	}
	scores := make(map[string]float32, len(resp.Scores))
	for _, s := range resp.Scores {
		scores[s.Label] = s.Value
	}
	return &ClassifyImageResult{
		Scores:    scores,
		Action:    resp.Action,
		Reason:    resp.Reason,
		LatencyMs: resp.LatencyMs,
	}, nil
}

// ClassifyTextResult is a simplified result from the text classifier.
type ClassifyTextResult struct {
	Scores    map[string]float32
	Action    classifyv1.Action
	Reason    string
	LatencyMs int64
}

// ClassifyText calls the text-classifier service with the given text.
func (c *Clients) ClassifyText(ctx context.Context, text string, requestID string) (*ClassifyTextResult, error) {
	resp, err := c.Text.Classify(ctx, &classifyv1.ClassifyTextRequest{
		Text:      text,
		RequestId: requestID,
	})
	if err != nil {
		return nil, err
	}
	scores := make(map[string]float32, len(resp.Scores))
	for _, s := range resp.Scores {
		scores[s.Label] = s.Value
	}
	return &ClassifyTextResult{
		Scores:    scores,
		Action:    resp.Action,
		Reason:    resp.Reason,
		LatencyMs: resp.LatencyMs,
	}, nil
}
