# Image classifier

Python + ONNX Runtime gRPC service. Loads the configured model
(`classifiers.image.model`) and returns per-class scores for each input image.
JPEG, PNG, GIF, WebP, and AVIF are decoded through Pillow. SVG inputs are
rasterized to the model input size before scoring.

**M0:** Dockerfile stub only.

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
