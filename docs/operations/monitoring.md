<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Monitoring

The Compose stack's metrics, logs and dashboards, and the stateful-feature kill-switch runbook
([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md)). Kubernetes:
[cluster monitoring](../../deploy/observability/README.md).

## Components

Host ports bind to `127.0.0.1`.

| Name | Role | Port | Config |
|---|---|---|---|
| `prometheus` | Metrics store, 15 s scrape and evaluation interval | `${PROMETHEUS_PORT:-9090}` | `infrastructure/prometheus/prometheus.yml` |
| `grafana` | Dashboards | `${GRAFANA_PORT:-3000}` (`.env.example`: `3001`) | `infrastructure/grafana/provisioning/` |
| `loki` | Log store | `3100` | `infrastructure/loki/loki-config.yml` |
| `promtail` | Ships container and `/var/log` logs to Loki | none (`9080` internal) | `infrastructure/promtail/promtail-config.yml` |
| `cadvisor` | Container CPU, memory, network | `8080` | flags in `docker-compose.yml` |
| `node-exporter` | Host CPU, memory, disk, network | `9100` | flags in `docker-compose.yml` |
| `postgres-exporter` | TimescaleDB metrics | `9187` | env in `docker-compose.yml` |
| `redis-exporter` | Redis metrics | `9121` | env in `docker-compose.yml` |
| `kafka-exporter` | Kafka topic offsets, consumer lag | `9308` | flags in `docker-compose.yml` |

| Volume | Holds |
|---|---|
| `prometheus-data` | TSDB, retention `30d` (`--storage.tsdb.retention.time` in `docker-compose.yml`) |
| `grafana-data` | Grafana state |
| `loki-data` | Chunks, index, compactor |
| `promtail-positions` | Read positions; a recreated `promtail` resumes instead of re-sending history Loki rejects as too old |

## Prometheus scrape jobs

| Job | Target | Path |
|---|---|---|
| `prometheus` | `localhost:9090` | `/metrics` |
| `cadvisor` | `cadvisor:8080` | `/metrics` |
| `node-exporter` | `node-exporter:9100` | `/metrics` |
| `postgres` | `postgres-exporter:9187` | `/metrics` |
| `redis` | `redis-exporter:9121` | `/metrics` |
| `api-gateway` | `api-gateway:8000` | `/metrics` |
| `ml-service` | `ml-service-api:8002` | `/metrics` |
| `ingestion-service` | `ingestion-edge:9003` (internal metrics-only listener; `/metrics` is denied on the device port) | `/metrics` |
| `kafka` | `kafka-exporter:9308` | `/metrics` |
| `spark-driver` | `spark-streaming:4040`, `spark-aggregations:4040` | `/metrics/prometheus` |
| `spark-executors` | `spark-streaming:4040`, `spark-aggregations:4040` | `/metrics/executors/prometheus` |
| `spark-master` | `spark-master:8080` | `/metrics/master/prometheus` |
| `loki` | `loki:3100` | `/metrics`; `up{job="loki"}` is Loki's health signal (distroless image, no container healthcheck) |
| `promtail` | `promtail:9080` | `/metrics` |

## Grafana

| Item | Value |
|---|---|
| URL | `http://localhost:<GRAFANA_PORT>` |
| Login | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` from `.env` |
| Datasources | `Prometheus` (default), `Loki` — `datasources.yml` |
| Dashboard provider | folder `IndustryFlow`, files under `/etc/grafana/provisioning/dashboards` — `dashboards.yml` |

There is no database datasource: the database holds tenant data
([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 5). Database
health reaches Grafana as `postgres-exporter` metrics.

### Dashboards

| UID | Title | File | Rows / panels |
|---|---|---|---|
| `complete-system` | Complete System Infrastructure | `complete_system_dashboard.json` | Service health (`up` for prometheus, postgres, redis, kafka, api-gateway, ml-service; alert detector via cAdvisor `container_last_seen`); system CPU / memory / disk; container CPU and memory; container and host network I/O; DB connections and activity; Redis hit rate and activity; Kafka topic offsets and consumer lag; Spark container CPU / memory / network. Refresh `10s` |
| `container_logs` | Container Logs Dashboard | `logs_dashboard.json` | Log rate by service, active containers, current log rate, top 10 services by volume, log viewer. Variables `service`, `container`, `search` (regex). Refresh `10s` |

## Metrics

| Source | Metrics |
|---|---|
| `api-gateway`, `ml-service-api`, `ingestion-service` (`prometheus-fastapi-instrumentator` defaults) | `http_requests_total{handler,method,status}` (`status` grouped: `2xx`, `5xx`, …), `http_request_duration_seconds{handler,method}`, `http_request_duration_highr_seconds`, `http_request_size_bytes`, `http_response_size_bytes` |
| `ml-service-api` | `ml_stateful_features_neutralized_total` — see [kill-switch](#runbook-stateful-feature-kill-switch) |
| Spark | Native `PrometheusServlet` driver, executor and master metrics |
| `cadvisor` | `container_cpu_usage_seconds_total`, `container_memory_usage_bytes`, `container_network_{receive,transmit}_bytes_total`, `container_last_seen` (disk and diskIO metrics disabled) |
| `node-exporter` | `node_cpu_seconds_total`, `node_memory_*`, `node_filesystem_*`, `node_network_*` |
| `postgres-exporter` | `pg_up`, `pg_stat_database_*`, `pg_database_size_bytes` |
| `redis-exporter` | `redis_up`, `redis_connected_clients`, `redis_commands_total` |
| `kafka-exporter` | `kafka_brokers`, `kafka_topic_partition_current_offset`, `kafka_consumergroup_lag` |

`postgres-exporter` connects to `timescaledb` as `metrics_user` (`pg_monitor`) with
`METRICS_DB_PASSWORD`, `sslmode=${DB_SSLMODE:-verify-full}`
([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 3,
[ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md)).

| Question | PromQL |
|---|---|
| Request rate | `sum by (handler) (rate(http_requests_total[5m]))` |
| 5xx rate | `sum by (job) (rate(http_requests_total{status="5xx"}[5m]))` |
| p95 latency | `histogram_quantile(0.95, sum by (le, handler) (rate(http_request_duration_seconds_bucket[5m])))` |
| Container CPU | `sum by (name) (rate(container_cpu_usage_seconds_total{name=~".+"}[5m]))` |
| Host memory used % | `100 * (1 - node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes)` |
| DB connections | `pg_stat_database_numbackends{datname="industryflow"}` |
| Consumer lag | `sum by (consumergroup, topic) (kafka_consumergroup_lag)` |
| Dead-lettered readings | `sum by (topic) (increase(kafka_topic_partition_current_offset{topic=~".+\\.dlq"}[15m]))` |

## Logs

| Setting | Value (`loki-config.yml`) |
|---|---|
| Retention | `168h`, compactor every `10m` |
| Ingestion rate / burst | `16` / `32` MB |
| Per-stream rate / burst | `8MB` / `16MB` |
| Max streams per user | `10000` local, `50000` global |
| Auth | disabled (`auth_enabled: false`) |

| Promtail job | Source | Labels |
|---|---|---|
| `docker` | Docker socket, containers on `industryflow-network` and `industryflow-notebook-kernels` only | `job="docker"`, `container`, `container_id`, `service`, `project`, `level` (from JSON logs), `environment="production"`, `cluster="industryflow"` |
| `system` | `/var/log/*log` | `job="varlogs"` |

| Query | LogQL |
|---|---|
| One service | `{service="api-gateway"}` |
| One container | `{container="industryflow-kafka"}` |
| Errors | `{job="docker"} \|~ "ERROR\|FATAL"` |
| JSON level | `{job="docker", level="error"}` |
| Lines per service | `sum by (service) (count_over_time({job="docker"}[5m]))` |
| Noisiest containers | `topk(10, sum by (container) (count_over_time({job="docker"}[1h])))` |

| Loki API | Path |
|---|---|
| Ready | `GET http://localhost:3100/ready` |
| Labels | `GET http://localhost:3100/loki/api/v1/labels` |
| Label values | `GET http://localhost:3100/loki/api/v1/label/<label>/values` |
| Range query | `GET http://localhost:3100/loki/api/v1/query_range?query=<logql>` |

## Alerts

| Plane | Where |
|---|---|
| Platform rules, Compose | None: `prometheus.yml` has no `rule_files` and no Alertmanager target |
| Platform rules, Kubernetes | Helm `PrometheusRule` ([platform alert rules](../../deploy/observability/README.md#platform-alert-rules)) |
| Tenant sensor and streaming alerts | `alert-service-detector`, tenant-isolated, never this stack ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 5) |

## Runbook: stateful-feature kill-switch

The switch ([ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)) makes the feature engine
fill every stateful transform's slot (`statistical`, `rolling_stat`) with its neutral value without
calling the transform, so inference stops reading the aggregate tables. Vector length and order are
unchanged. Use it when the database is alive but overloaded; in a total outage it reads as enabled
and the transforms fall back to the same neutral value.

| Task | Command / signal |
|---|---|
| Turn off (neutralize) | `UPDATE public.platform_config SET value = 'false'::jsonb, updated_at = NOW(), updated_by = '<operator>' WHERE key = 'stateful_features_enabled';` |
| Turn back on | same statement with `'true'::jsonb` |
| Takes effect | within `STATEFUL_SWITCH_CACHE_TTL` seconds (default `5`); no restart |
| Confirm, response | `POST /api/inference/predict` returns `degraded: true` and `neutralized_features` |
| Confirm, metric | `rate(ml_stateful_features_neutralized_total[5m])` > 0; `0` in normal operation |

`ml_service_user` holds `SELECT` only on `public.platform_config`; the service never writes the flag.
No shipped rule alerts on the counter.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Target down in Prometheus | Target unreachable or failing; `curl -s localhost:9090/api/v1/targets \| jq '.data.activeTargets[] \| {job: .labels.job, health, lastError}'` | Fix the target named in `lastError` |
| Edit to `prometheus.yml` not applied | The single-file bind mount keeps the old inode when an editor replaces the file | `docker exec industryflow-prometheus promtool check config /etc/prometheus/prometheus.yml`; `docker compose restart prometheus` |
| Dashboards missing | Dashboard files are bind-mounted one by one in `docker-compose.yml`; a new JSON file has no mount | Add its mount, then `docker compose up -d grafana` |
| No logs in Loki | Container not on `industryflow-network` or `industryflow-notebook-kernels`, or promtail down (`docker logs industryflow-promtail`) | Attach the container to one of the networks; `curl -s localhost:3100/ready` |
| `pg_up` is `0` | `metrics_user` has no password, or the CA mount for `verify-full` is missing (`docker logs industryflow-postgres-exporter`) | Set the password as `12-metrics-role.sql` describes; check the CA mount |
| `redis_up` is `0` | Redis down (`docker exec industryflow-redis redis-cli ping`) | Start `redis`, then the exporter |
| LogQL parse error | Unquoted label value or range without a unit | `{job="docker"}`, `[5m]` |

## Links

- [Prometheus](https://prometheus.io/docs/) · [PromQL](https://prometheus.io/docs/prometheus/latest/querying/basics/) · [Grafana](https://grafana.com/docs/) · [Loki](https://grafana.com/docs/loki/) · [LogQL](https://grafana.com/docs/loki/latest/query/)
