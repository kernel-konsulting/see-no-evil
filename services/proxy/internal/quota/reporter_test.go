package quota

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

func TestReporterAccumulateAndFlush(t *testing.T) {
	var (
		mu      sync.Mutex
		got     []map[string]any
		gotAuth []string
	)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/quota/heartbeat" {
			t.Errorf("unexpected path %q", r.URL.Path)
			w.WriteHeader(http.StatusNotFound)
			return
		}
		mu.Lock()
		gotAuth = append(gotAuth, r.Header.Get("Authorization"))
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("bad body: %v", err)
		}
		got = append(got, body)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"device_id":1,"day":"2026-01-01","minutes_used":5,"minutes_quota":60}`))
	}))
	defer srv.Close()

	r := NewReporter(srv.URL, "tok")
	r.NoteActivity("192.168.1.10")
	r.NoteActivity("192.168.1.11")
	r.accumulate() // 1 minute each
	r.NoteActivity("192.168.1.10")
	r.accumulate() // +1 minute for .10

	r.flush(context.Background())

	mu.Lock()
	defer mu.Unlock()
	if len(got) != 2 {
		t.Fatalf("expected 2 heartbeats, got %d (%v)", len(got), got)
	}
	byIP := map[string]int{}
	for _, b := range got {
		ip, _ := b["client_ip"].(string)
		mins, _ := b["minutes"].(float64)
		byIP[ip] = int(mins)
	}
	if byIP["192.168.1.10"] != 2 {
		t.Errorf("ip .10 minutes = %d, want 2", byIP["192.168.1.10"])
	}
	if byIP["192.168.1.11"] != 1 {
		t.Errorf("ip .11 minutes = %d, want 1", byIP["192.168.1.11"])
	}
	for _, a := range gotAuth {
		if a != "Bearer tok" {
			t.Errorf("Authorization = %q, want Bearer tok", a)
		}
	}
	// Counters reset after successful flush.
	r.mu.Lock()
	defer r.mu.Unlock()
	if len(r.minutes) != 0 {
		t.Errorf("minutes not cleared after flush: %v", r.minutes)
	}
}

func TestReporterKeepsCountersOnFailure(t *testing.T) {
	fail := true
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if fail {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	r := NewReporter(srv.URL, "")
	r.NoteActivity("192.168.1.10")
	r.accumulate()
	r.flush(context.Background())

	r.mu.Lock()
	kept := r.minutes["192.168.1.10"]
	r.mu.Unlock()
	if kept != 1 {
		t.Fatalf("minutes = %d after failed flush, want 1 (retained)", kept)
	}

	fail = false
	r.flush(context.Background())
	r.mu.Lock()
	after := r.minutes["192.168.1.10"]
	r.mu.Unlock()
	if after != 0 {
		t.Fatalf("minutes = %d after successful flush, want 0", after)
	}
}

func TestReporterRunTick(t *testing.T) {
	var calls int
	var mu sync.Mutex
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		calls++
		mu.Unlock()
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	r := NewReporter(srv.URL, "")
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		r.Run(ctx, 50*time.Millisecond)
		close(done)
	}()
	// Keep the IP active so every tick accumulates a minute and posts.
	stopActivity := make(chan struct{})
	go func() {
		for {
			select {
			case <-stopActivity:
				return
			default:
				r.NoteActivity("192.168.1.10")
				time.Sleep(20 * time.Millisecond)
			}
		}
	}()
	time.Sleep(220 * time.Millisecond)
	cancel()
	<-done
	close(stopActivity)

	mu.Lock()
	defer mu.Unlock()
	if calls < 2 {
		t.Fatalf("expected >=2 heartbeats over ~4 ticks, got %d", calls)
	}
}
