# Backup & restore

see-no-evil keeps everything stateful under `/data` inside the API container:

| Path | Purpose |
|---|---|
| `/data/policy.db` | SQLite database (profiles, devices, audit, sessions) |
| `/data/ca/` | The MITM CA private key + certificate |
| `/data/models/` | Cached ONNX models (re-fetchable but pinned by checksum) |

Two complementary backup strategies are supported:

## 1. Local snapshots (`backup` profile)

Tarball `/data` to `backup.local_path` on a schedule. Good for an external
drive, NFS mount, or something `rsync`-able to off-box storage.

```bash
docker compose --profile core --profile backup up -d
```

`config.yaml`:

```yaml
backup:
  local_path: /data/backups
  interval: 24h        # informational; sidecar uses SNE_BACKUP_INTERVAL_SECONDS
  retention: 14        # keep at most N tarballs
```

Tune the sidecar interval via env var:

```bash
SNE_BACKUP_INTERVAL_SECONDS=21600 docker compose --profile core --profile backup up -d
```

You can also run snapshots ad-hoc:

```bash
docker compose exec api seenoevil-backup snapshot
docker compose exec api seenoevil-backup list
docker compose exec api seenoevil-backup restore /data/backups/seenoevil-20260101T000000Z.tar.gz
```

Restore extracts over `pod.data_dir`. **Stop the API first** so SQLite isn't
holding the file:

```bash
docker compose stop api
docker compose run --rm api seenoevil-backup restore /data/backups/<archive>.tar.gz
docker compose start api
```

## 2. Continuous replication (`litestream` profile)

For point-in-time recovery without operator action, pair the DB with
[Litestream](https://litestream.io/) replicating to S3 (or any
S3-compatible target like Backblaze B2, MinIO, or Wasabi).

```bash
LITESTREAM_ACCESS_KEY_ID=... \
LITESTREAM_SECRET_ACCESS_KEY=... \
LITESTREAM_BUCKET=my-bucket \
docker compose --profile core --profile litestream up -d
```

`config.yaml` (for visibility — Litestream itself is configured by
`services/api/litestream.yml`):

```yaml
litestream:
  enabled: true
  replica_url: s3://my-bucket/policy.db
```

Edit `services/api/litestream.yml` to point at your bucket. Litestream
streams WAL frames every `sync-interval` (default 10s) and uploads a full
snapshot every `snapshot-interval` (default 24h).

To restore from S3:

```bash
docker run --rm \
  -e LITESTREAM_ACCESS_KEY_ID=... \
  -e LITESTREAM_SECRET_ACCESS_KEY=... \
  -v seenoevil_data:/data \
  litestream/litestream:0.3 \
  restore -o /data/policy.db s3://my-bucket/policy.db
```

## Pick one — or both

The two profiles are independent. Litestream gives you point-in-time
recovery; local snapshots give you an offline tarball you can carry off-box.
For a home pod, `backup` alone is usually enough; for a non-trivial
deployment, run both.
