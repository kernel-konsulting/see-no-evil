# Video sampler

Go + ffmpeg gRPC service. Extracts N evenly-spaced frames from a video stream
and forwards each to the image classifier. If any frame trips the threshold for
its profile, the whole video is dropped.

**M0:** Dockerfile stub only.

## Tunables

- `classifiers.video.sample_frames` (default 8)
- `classifiers.video.max_video_size` (default 50 MiB; larger videos pass through)
