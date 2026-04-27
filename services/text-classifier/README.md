# Text classifier

Python + ONNX Runtime gRPC service. Classifies URL paths and inspected response
bodies (HTML, JSON) for toxicity / NSFW text.

**M0:** Dockerfile stub only.

## Default model

`unitary/toxic-bert` exported to ONNX. Returns scores for `toxic`, `severe_toxic`,
`obscene`, `threat`, `insult`, `identity_hate`. Per-profile thresholds live in
`profiles[].text_thresholds`.
