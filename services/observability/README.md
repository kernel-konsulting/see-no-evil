# Observability profile

Optional metrics + log + alerting pipeline for see-no-evil. Enable with:

```bash
GRAFANA_ADMIN_PASSWORD='change-me' \
  docker compose --profile core --profile observability up -d
```

## Components

| Service | Port | Role |
|---|---|---|
| `vector` | — | Scrapes Prometheus endpoints + container logs |
| `victoriametrics` | 8428 | Time-series DB (Prometheus-compatible) |
| `vmalert` | — | Evaluates `alerts.yml`, posts firing alerts to the API webhook |
| `cadvisor` | — | Per-container CPU / memory, surfaced via Vector |
| `grafana` | 3000 | Dashboards |

## Provisioning

`vector.toml` defines scrape jobs (api, proxy, image-classifier,
text-classifier, video-sampler, scanner, dns, cadvisor) and the
VictoriaMetrics + Parquet sinks.

Grafana auto-loads:

- `grafana/provisioning/datasources/victoriametrics.yaml` — VM data source
- `grafana/provisioning/dashboards/seenoevil.yaml` — dashboard provider
- `grafana/provisioning/dashboards/files/overview.json` — health + decisions
- `grafana/provisioning/dashboards/files/classifiers.json` — image / text /
  video latency + errors
- `grafana/provisioning/dashboards/files/host.json` — container CPU / RAM /
  DB / DNS

`alerts.yml` ships with three rule groups:

- **availability** — proxy / classifiers / API down
- **quality** — p95 latency, classifier error bursts, growing quarantine
  queue, panic-relax active
- **backup** — backup older than 48 h

vmalert forwards firing alerts to `POST /v1/alerts/webhook` on the API,
which fans them out through the same `notifications:` config (ntfy +
webhook) used for block notifications.

## Defaults

- VictoriaMetrics retention: 30 days (override via `VM_RETENTION_PERIOD`)
- Grafana admin password: read from `${GRAFANA_ADMIN_PASSWORD}` (compose
  refuses to start without it set)
- vmalert evaluation interval: 30 s
- Vector / VM / vmalert run on the `internal` network — no external egress

## Adding your own dashboards

Drop `*.json` files into `grafana/provisioning/dashboards/files/`. Grafana
picks them up within `updateIntervalSeconds` (30 s).

## Status

**M9 implemented.** Three dashboards, three alert groups, Vector scrapes
every shipped service plus cAdvisor for container-level metrics.
