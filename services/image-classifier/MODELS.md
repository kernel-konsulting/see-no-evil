# Image classifier — model registry (M0 placeholder)

Pinned model revisions and SHA-256 checksums. Updated alongside
`updates.models.image_revision` in `config.example.yaml`.

| Revision | Source | SHA-256 | Size | Notes |
|---|---|---|---|---|
| v1.0.0 | Freepik/nsfw_image_detector | `TBD-M1` | ~310 MiB | default |
| v1.0.0 | Falconsai/nsfw_image_detection | `TBD-M1` | ~85 MiB | faster, binary only |

Checksums will be filled in at M1 once the updater code lands. The updater
verifies each downloaded file and refuses to start the classifier if a
checksum mismatches.
