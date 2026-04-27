# Observability profile

Optional metrics + log pipeline for see-no-evil. Enable with:

```bash
docker compose --profile core --profile observability up -d
```

## Components

| Service | Port | Role |
|---|---|---|
| `vector` | — | Scrapes Prometheus endpoints + container logs |
| `victoriametrics` | 8428 | Time-series DB (Prometheus-compatible) |
| `grafana` | 3000 | Dashboards |

## Provisioning

`vector.toml` defines the scrape jobs and sinks. Grafana auto-loads:

- `grafana/provisioning/datasources/victoriametrics.yaml` — VM data source
- `grafana/provisioning/dashboards/seenoevil.yaml` — dashboard provider
- `grafana/provisioning/dashboards/files/overview.json` — overview dashboard

## Defaults

- VictoriaMetrics retention: 30 days (override via `VM_RETENTION_PERIOD` env)
- Grafana admin password: read from `${GRAFANA_ADMIN_PASSWORD}` env (compose
  refuses to start without it set)
- Vector config bind-mounted read-only

## Status

**M1.6 implemented.** Dashboards intentionally minimal; expand in M3+ as more
service metrics are exported.
