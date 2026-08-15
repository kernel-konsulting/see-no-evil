# see-no-evil — Hardware sizing

> **Methodology.** The numbers below come from the smoke-test harness at
> `tests/perf/` running each component on idle hardware with stock models
> (`freepik` image, `unitary/toxic-bert` text). For your own deployment,
> rerun the harness on the target host — see [Benchmarking your pod](#benchmarking-your-pod)
> at the bottom. Estimates are conservative; real-world numbers vary with
> page-image counts and video share of traffic.

## Minimum (toy / single user)

- **CPU:** 4 cores, x86-64-v3 or ARMv8.2-A (Raspberry Pi 5 OK)
- **RAM:** 4 GiB
- **Disk:** 16 GiB SSD (models + lists + 30 days of audit logs ≈ 6 GiB)
- **Network:** 100 Mbit (proxy and classifiers will saturate this on a single
  4K video stream — image inspection is the bottleneck)
- **Throughput:** ~1–2 concurrent inspected HTTPS streams

Suitable for: one or two filtered devices, demos, dev work.

## Recommended (typical family / small school class, ~10–25 devices)

- **CPU:** 6–8 cores, x86-64-v3 (Intel N100, AMD Ryzen 5 4500U, or similar)
- **RAM:** 8 GiB
- **Disk:** 64 GiB NVMe
- **Network:** 1 Gbit
- **Throughput:** ~10–15 concurrent inspected HTTPS streams; comfortable for
  20–30 mostly-idle devices.

Suitable for: most home deployments. A used Lenovo M75q or similar mini-PC is
the sweet spot.

## Org / non-profit (~50–200 devices)

- **CPU:** 16+ cores, modern x86-64 (Ryzen 7 7700, Xeon E-2400, etc.)
- **RAM:** 32 GiB
- **Disk:** 256 GiB NVMe (audit log retention dominates)
- **GPU (recommended):** any CUDA-capable GPU with 8+ GiB VRAM, **or** an
  Intel iGPU with OpenVINO support, **or** Apple silicon with CoreML.
  This moves image classification off the CPU and 5–10× the throughput.
- **Network:** 1–10 Gbit
- **DB:** Postgres (set `db.url`).
- **Cache:** Redis (set `cache.kind: redis`).
- **Throughput:** ~50–100 concurrent inspected HTTPS streams.

Suitable for: school computer lab, small library, non-profit office.

## Notes on bottlenecks

1. **Image classification dominates.** Expect ~20–60 ms per image on CPU
   (Freepik model, ONNX Runtime, AVX2). A typical web page yields 5–30
   inspectable images. GPU drops this to 2–5 ms.
2. **Video sampling is bursty.** Default 8 frames per video, decoded with
   ffmpeg; worst case (50 MiB video, 8 keyframe extractions) ≈ 1–2 seconds
   wall-clock added latency *for that response only*.
3. **Text classification is cheap.** ~5–10 ms per page on CPU.
4. **DNS is essentially free** at these scales — Blocky handles 10k+ qps on a
   single core.
5. **The DB is fine on SQLite** until you exceed ~50 sustained
   request-decisions per second, after which switch to Postgres.

## What slows things down most

1. Inspecting large image-heavy pages without a GPU.
2. CPU throttling on small ARM SBCs under sustained load (Pi 5 will get
   uncomfortably warm — use a heatsink).
3. Disk I/O on SD cards. Don't use SD cards for `pod.data_dir`.
4. Running everything on one Wi-Fi-connected mini-PC — wire it.

## Power draw (rough)

- Pi 5 minimum: ~7 W idle, ~12 W under load.
- Mini-PC recommended: ~10 W idle, ~30 W under load.
- Org build with GPU: 50–150 W under load depending on GPU.

## Benchmarking your pod

The Grafana dashboards shipped under the `observability` profile expose
everything you need to size for your workload:

1. Bring the stack up with `--profile observability`.
2. Run a representative client workload for at least an hour (a kid
   browsing YouTube, a couple of streaming sessions, etc.).
3. Open the **classifiers** dashboard and read off:
   - p95 image-classifier latency — should stay well under 200 ms on CPU,
     under 30 ms on GPU.
   - Proxy p95 latency — under 1 s for browsing to feel snappy.
4. Open the **host & runtime** dashboard:
   - Per-container CPU should stay under 70 % of one core sustained.
   - RSS for the image classifier is the largest single number; the ONNX
     model is ~340 MB in RAM on top of the runtime. If memory is tight,
     prefer a machine with more RAM or run with the model on a swap-backed
     volume rather than the (removed) `falconsai` variant, which shipped no
     ONNX export.

Alert rules in `services/observability/alerts.yml` will fire if proxy
latency or classifier errors blow past sensible defaults — adjust the
thresholds to match your hardware once you have a baseline.

## Recommended preset by host class

| Host | Image model | `device` | Video sample rate | DB |
|---|---|---|---|---|
| Pi 4 | `freepik` | `cpu` | 4 frames | SQLite |
| Pi 5 / N100 | `freepik` | `cpu` | 8 frames | SQLite |
| Mini-PC + iGPU | `freepik` | `openvino` | 8 frames | SQLite |
| Workstation + NVIDIA | `freepik` | `cuda` | 16 frames | SQLite or Postgres |
| Org server + GPU | `freepik` (FP16) | `cuda` | 16 frames | Postgres |
