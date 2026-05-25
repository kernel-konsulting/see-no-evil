# Image classifier

Python + ONNX Runtime gRPC service. Loads the configured model
(`classifiers.image.model`) and returns per-class scores for each input image.
JPEG, PNG, GIF, WebP, and AVIF are decoded through Pillow. SVG inputs are
rasterized to the model input size before scoring.

Implemented today:

- gRPC `ImageClassifier` service with per-label scores and block/allow actions.
- Pillow-backed decode path for JPEG, PNG, GIF, WebP, AVIF, and SVG.
- Prometheus metrics and health endpoint wiring.
- Local regression harness that pushes the `test_data/` corpus through the
  real proxy, reads scores back from `/v1/audit`, and writes a static HTML
  report plus JSON snapshot for baseline comparisons.

## Models

- `freepik` — [Freepik/nsfw_image_detector](https://huggingface.co/Freepik/nsfw_image_detector)
  multi-class (porn / hentai / sexy / drawing / neutral). Default.
- `falconsai` — [Falconsai/nsfw_image_detection](https://huggingface.co/Falconsai/nsfw_image_detection)
  binary, smaller / faster.

Weights are pulled by the `updater` container on first start, verified against
checksums shipped in `MODELS.md`, and cached to `${pod.data_dir}/models/image/`.

## Tuning

Per-profile thresholds live in `profiles[].image_thresholds` and override the
global defaults under `classifiers.image.thresholds`.

## Proxy Regression Harness

Use this when you want to see whether proxy/model behaviour drifted after a
threshold change, preprocessing tweak, model upgrade, or proxy refactor.

Prerequisites:

- The proxy is running and reachable, typically at `http://127.0.0.1:8080`.
- The API is running and reachable, typically at `http://127.0.0.1:8000`.
- You have a valid UI/API login because the harness reads `/v1/audit`.

For the Podman dev stack, the shortest path is now the reusable test wrapper:

```bash
./tests/proxy-regression/run.sh --write-baseline --view
```

That script builds missing images, starts the `seenoevil` pod if needed, runs
the regression harness inside the `image-classifier` container image, and can
open the generated `latest.html` report. The image corpus itself can stay
ignored under `test_data/`.

Run it from this service directory:

```bash
pip install -e ".[test]"
seenoevil-image-regression \
  --username admin@example.local \
  --password changeme \
  --write-baseline
```

What it does:

- Starts a tiny local static server for the files in `../../test_data/`.
- Requests each image through the configured proxy.
- Waits for the proxy's `/v1/decide` audit rows to land in the API.
- Saves `test_data/proxy-regression/latest.json` and `latest.html`.
- Optionally writes `test_data/proxy-regression/baseline.json`.

Useful flags:

- `--baseline <path>`: compare against a specific saved snapshot.
- `--write-baseline`: accept the current run as the new baseline.
- `--fail-on-change`: exit non-zero when decisions or scores drift beyond the
  configured tolerance.
- `--score-tolerance 0.02`: ignore tiny float noise below the given absolute delta.

Example CI-style smoke check against an existing baseline:

```bash
seenoevil-image-regression \
  --username admin@example.local \
  --password changeme \
  --baseline ../../test_data/proxy-regression/baseline.json \
  --fail-on-change
```

This harness measures score and decision drift. It does **not** tell you
whether a change is objectively better or worse by itself because the corpus
does not currently carry ground-truth labels.
