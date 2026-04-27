# Video sampler

Go + ffmpeg gRPC service. Receives a streamed `video/*` body from the proxy,
extracts N evenly-spaced keyframes via `ffmpeg -vf thumbnail,scale=...`, fans
each frame out to the image classifier, and reduces the per-frame results into
a single decision:

- any frame `BLOCK` → BLOCK (with reason `video:<frame-reason>`)
- else any frame `WARN` → WARN
- otherwise ALLOW

Per-label scores reported back are the **max** seen across frames.

## Behaviour

- gRPC server on `:50053` (configurable via `VIDEO_SAMPLER_PORT`)
- Prometheus metrics on `:9103`
- Streams video chunks from the proxy and writes them to a temp file capped at
  `VIDEO_SAMPLER_MAX_BYTES` (default 50 MiB). Anything bigger is **fail-open**
  to keep large legitimate uploads from stalling the proxy.
- `ffmpeg` invocation: `ffmpeg -y -i <tmp.mp4> -vf "thumbnail,scale=<frames>:-1" -frames:v <frames> -f image2 -q:v 5 frame-%03d.jpg`
- Image classifier dial address: `IMAGE_CLASSIFIER_ADDR` (default `image-classifier:50051`)
- ffmpeg failures (missing binary, malformed video, decode error) → ALLOW with
  reason `video_sampler:ffmpeg_failed`. The proxy never blocks because of
  infrastructure faults.

## Tunables

| Env var | Default | Meaning |
|---|---|---|
| `VIDEO_SAMPLER_PORT` | `50053` | gRPC listen port |
| `IMAGE_CLASSIFIER_ADDR` | `image-classifier:50051` | upstream classifier |
| `VIDEO_SAMPLER_MAX_FRAMES` | `8` | frames per video |
| `VIDEO_SAMPLER_MAX_BYTES` | `52428800` (50 MiB) | per-video cap |
| `FFMPEG_PATH` | `ffmpeg` | binary path |

## Metrics

- `video_sampler_requests_total{action}`
- `video_sampler_frame_latency_seconds`
- `video_sampler_ffmpeg_errors_total`
- `video_sampler_bytes_total`

## Quarantine previews

When the **proxy** blocks an image, it pipes the response body through
`internal/preview` to produce a 192-px-wide, heavily blurred JPEG which is sent
to the API as `thumbnail_b64` on the audit record. The quarantine UI can
display this safely (see `services/api/src/seenoevil_api/routers/decide.py`).
The video sampler does not yet return a thumbnail frame — that is tracked for
a future iteration.
