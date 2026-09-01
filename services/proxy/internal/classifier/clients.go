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
	Video classifyv1.VideoSamplerClient

	conns []*grpc.ClientConn
}

// NewClientsFromAddrs dials the classifier gRPC services at the given addresses.
// videoAddr may be empty to skip video classification entirely.
func NewClientsFromAddrs(imageAddr, textAddr, videoAddr string) (*Clients, error) {
	imgConn, err := dial(imageAddr)
	if err != nil {
		return nil, fmt.Errorf("dial image-classifier at %s: %w", imageAddr, err)
	}
	txtConn, err := dial(textAddr)
	if err != nil {
		_ = imgConn.Close()
		return nil, fmt.Errorf("dial text-classifier at %s: %w", textAddr, err)
	}
	c := &Clients{
		Image: classifyv1.NewImageClassifierClient(imgConn),
		Text:  classifyv1.NewTextClassifierClient(txtConn),
		conns: []*grpc.ClientConn{imgConn, txtConn},
	}
	if videoAddr != "" {
		vidConn, verr := dial(videoAddr)
		if verr != nil {
			_ = imgConn.Close()
			_ = txtConn.Close()
			return nil, fmt.Errorf("dial video-sampler at %s: %w", videoAddr, verr)
		}
		c.Video = classifyv1.NewVideoSamplerClient(vidConn)
		c.conns = append(c.conns, vidConn)
	}
	return c, nil
}

func dial(addr string) (*grpc.ClientConn, error) {
	const maxMsg = 64 << 20 // 64 MiB
	var lastErr error
	for attempt := 0; attempt < 5; attempt++ {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		//nolint:staticcheck // grpc.DialContext deprecated but replacement (NewClient) is not in all versions
		conn, err := grpc.DialContext(ctx, addr,
			grpc.WithTransportCredentials(insecure.NewCredentials()),
			grpc.WithBlock(),
			grpc.WithDefaultCallOptions(
				grpc.MaxCallRecvMsgSize(maxMsg),
				grpc.MaxCallSendMsgSize(maxMsg),
			),
		)
		cancel()
		if err == nil {
			return conn, nil
		}
		lastErr = err
		// Backoff 500ms, 1s, 2s, 4s
		backoff := time.Duration(500*(1<<attempt)) * time.Millisecond
		if backoff > 4*time.Second {
			backoff = 4 * time.Second
		}
		time.Sleep(backoff)
	}
	return nil, lastErr
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

// SampleVideoResult is a simplified result from the video sampler.
type SampleVideoResult struct {
	Scores       map[string]float32
	Action       classifyv1.Action
	Reason       string
	FramesScored int32
	LatencyMs    int64
}

// SampleVideo streams videoData to the video-sampler service in fixed-size
// chunks and returns the worst-frame verdict. maxFrames=0 uses the server
// default. Returns nil result if the video sampler client is not configured.
func (c *Clients) SampleVideo(ctx context.Context, videoData []byte, maxFrames int32, requestID string) (*SampleVideoResult, error) {
	if c.Video == nil {
		return nil, nil
	}
	stream, err := c.Video.Sample(ctx)
	if err != nil {
		return nil, err
	}
	const chunkSize = 256 * 1024
	for offset := 0; offset < len(videoData); offset += chunkSize {
		end := offset + chunkSize
		if end > len(videoData) {
			end = len(videoData)
		}
		msg := &classifyv1.SampleVideoRequest{Chunk: videoData[offset:end]}
		if offset == 0 {
			msg.RequestId = requestID
			msg.MaxFrames = maxFrames
		}
		if err := stream.Send(msg); err != nil {
			return nil, err
		}
	}
	resp, err := stream.CloseAndRecv()
	if err != nil {
		return nil, err
	}
	scores := make(map[string]float32, len(resp.WorstScores))
	for _, s := range resp.WorstScores {
		scores[s.Label] = s.Value
	}
	return &SampleVideoResult{
		Scores:       scores,
		Action:       resp.Action,
		Reason:       resp.Reason,
		FramesScored: resp.FramesScored,
		LatencyMs:    resp.LatencyMs,
	}, nil
}
