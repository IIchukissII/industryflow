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
| `prometheus` | Metrics store, 15 s scrape and evaluation interval; evaluates the platform alert rules | `${PROMETHEUS_PORT:-9090}` | `infrastructure/prometheus/prometheus.yml`, `infrastructure/prometheus/rules/` |
| `alertmanager` | Groups and routes firing alerts; no receiver as shipped | `${ALERTMANAGER_PORT:-9093}` | `${ALERTMANAGER_CONFIG:-infrastructure/alertmanager/alertmanager.yml}` |
| `grafana` | Dashboards | `${GRAFANA_PORT:-3001}` | `infrastructure/grafana/provisioning/` |
| `loki` | Log store | `3100` | `infrastructure/loki/loki-config.yml` |
| `promtail` | Ships container and `/var/log` logs to Loki | none (`9080` internal) | `infrastructure/promtail/promtail-config.yml` |
| `cadvisor` | Container CPU, memory, network | `8080` | flags in `docker-compose.yml` |
| `node-exporter` | Host CPU, memory, disk, network | `9100` | flags in `docker-compose.yml` |
| `postgres-exporter` | TimescaleDB metrics | `9187` | env in `docker-compose.yml` |
| `redis-exporter` | Redis metrics | `9121` | env in `docker-compose.yml` |
| `kafka-exporter` | Kafka topic offsets, consumer lag | `9308` | flags in `docker-compose.yml` |
| `ops-agent` | Infrastructure agent: explains each platform alert in a note in Loki; profile `ops-agent`, off by default ([below](#infrastructure-agent)) | none (`8095` internal) | `OPS_AGENT_*` in `.env` |

| Volume | Holds |
|---|---|
| `prometheus-data` | TSDB, retention `30d` (`--storage.tsdb.retention.time` in `docker-compose.yml`) |
| `alertmanager-data` | Silences and notification log |
| `grafana-data` | Grafana state |
| `loki-data` | Chunks, index, compactor |
| `promtail-positions` | Read positions; a recreated `promtail` resumes instead of re-sending history Loki rejects as too old |

## Prometheus scrape jobs

Every target carries the label `platform="industryflow"`, which every platform alert rule selects on.

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
| `alertmanager` | `alertmanager:9093` | `/metrics` |

## Alerts

Prometheus evaluates [`infrastructure/prometheus/rules/platform.yml`](../../infrastructure/prometheus/rules/platform.yml),
the same file the chart renders into its PrometheusRule ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 1).

| Alert | Fires when | Handling |
|---|---|---|
| `IndustryFlowTargetDown` | A scrape target is down for 5m | `docker compose ps`; the service's logs |
| `IndustryFlowPostgresDown` | `postgres-exporter` cannot reach TimescaleDB for 5m | `docker compose logs timescaledb` |
| `IndustryFlowRedisDown` | `redis-exporter` cannot reach Redis for 5m | `docker compose logs redis` |
| `IndustryFlowRedisMemoryHigh` | Redis above 80% of its `maxmemory` for 10m; it never evicts, so at the limit mints, sign-ins and cache writes fail | `redis-cli info memory`; `redis-cli --bigkeys`; the capability-use stream's backlog (`capability_audit_backlog`) |
| `IndustryFlowKafkaConsumerLag` | A consumer group lags more than 1000 for 10m | The consumer's logs |
| `IndustryFlowDeadLetteredReadings` | A letter reached a `*.dlq` topic in the last 15m | [Dead-letter handling](dead-letters.md) |
| `IndustryFlowCapabilityAuditBacklog` | More than 1000 capability-use events wait to be recorded for 15m | The API gateway's logs (`capability audit writer`); [capability-use record](../architecture/notebooks.md#capability-use-record) |
| `IndustryFlowCapabilityEventsDropped` | The writer acknowledged a capability-use event it could not record (unreadable, or refused by the database) | The API gateway's logs (`capability audit`) name each stream id; [capability-use record](../architecture/notebooks.md#capability-use-record) |

| Where to see firing alerts | Address |
|---|---|
| Prometheus | `http://localhost:<PROMETHEUS_PORT>/alerts` |
| Alertmanager | `http://localhost:<ALERTMANAGER_PORT>` |

### Alert delivery

The shipped `infrastructure/alertmanager/alertmanager.yml` routes every alert to a receiver with no
integration: nothing is sent.

| Step | Action |
|---|---|
| 1 | Copy `infrastructure/alertmanager/alertmanager.yml` outside the repository and add an integration to receiver `operator` (`email_configs`, `webhook_configs`, …; [Alertmanager configuration](https://prometheus.io/docs/alerting/latest/configuration/)) |
| 2 | Set `ALERTMANAGER_CONFIG=<path>` in `.env` |
| 3 | `docker compose up -d alertmanager` |
| 4 | Check: `docker compose exec alertmanager amtool check-config /etc/alertmanager/alertmanager.yml` |

### Infrastructure agent

The `ops-agent` service receives Alertmanager's webhook, gathers evidence for each firing platform
alert and has a model write a short note: what happened, impact, likely cause with evidence, next
step from the runbooks ([ADR-0045](../../ADR/ADR-0045-agent-runtime.md),
[ADR-0046](../../ADR/ADR-0046-infrastructure-agent.md)). Notes are generated text, marked with the
model that wrote them; advice beside the alert, not a diagnosis.

| Step | Action |
|---|---|
| 1 | `.env`: `OPS_AGENT_WEBHOOK_SECRET=$(openssl rand -hex 32)`, the model settings below |
| 2 | In your `ALERTMANAGER_CONFIG` copy, add to receiver `operator` the webhook below |
| 3 | `docker compose --profile ops-agent up -d --build ops-agent` and `docker compose up -d alertmanager` |
| 4 | Check: `docker compose logs ops-agent` shows no `refusing to start`; Grafana › IndustryFlow › Incidents |

```yaml
receivers:
  - name: operator
    webhook_configs:
      - url: http://ops-agent:8095/alertmanager
        send_resolved: false
        http_config:
          authorization:
            type: Bearer
            credentials: <OPS_AGENT_WEBHOOK_SECRET>
```

| Setting | Default | Meaning |
|---|---|---|
| `OPS_AGENT_WEBHOOK_SECRET` | none | Bearer secret the receiver checks; at least 32 characters, or the agent refuses to start |
| `OPS_AGENT_MODEL_PROVIDER` | `anthropic` | `anthropic` (Claude), or `openai-compatible` (Mistral's API, or a model server on your own host) |
| `OPS_AGENT_MODEL` | `claude-opus-5-5` | Model name at the provider |
| `ANTHROPIC_API_KEY` | none | Key for `anthropic` |
| `OPS_AGENT_MODEL_BASE_URL`, `OPS_AGENT_MODEL_API_KEY` | none | Endpoint and key for `openai-compatible`, e.g. `https://api.mistral.ai/v1` or `http://<gpu-host>:8000/v1` |
| `OPS_AGENT_EFFORT` | `medium` | Claude's effort level |
| `OPS_AGENT_LOG_SELECTOR` | `{service="%s"}` | Loki selector for a service's logs; `%s` is the service name |

| Item | Value |
|---|---|
| Evidence | The rule's PromQL over the 30 minutes before the alert to now; error lines of the services in the rule's `services` annotation and the target's host; other active alerts; the alert's row in the [alert table](#alerts) (every platform rule has one); `Symptom \| Cause \| Fix` rows sharing at least two words with the alert. The metric series are narrowed to the alert's own labels |
| Sent to the model | The alert and the evidence, after redaction: tenant identifiers, emails, network addresses and credential-shaped strings become `<tenant-1>`, `<email-1>`, … ; the note records the counts per category |
| Output | Loki stream `{job="ops-agent"}`; the `complete-system` and Incidents dashboards draw it as annotations. No Grafana credential |
| Note fields | `alertname`, `severity`, `priority` (1 critical, 2 warning, 3 info, 4 other), `services`, `what`, `impact`, `cause`, `next_step` (the model's four sections), `text` (the whole note), `model`, `evidence`, `redacted`, `fingerprint`, `key` |
| Without a model | A model error or refusal still publishes a note: the evidence listing, `model` = `none` |
| Once per firing | A firing (fingerprint and start) is explained once; remembered 7 days, and after a restart the agent finds its earlier note in Loki |
| Alert storm | At most 5 alerts of one webhook (critical first) and 10 waiting; the rest are logged as not explained |
| Refuses to start | Webhook secret under 32 characters; `anthropic` without `ANTHROPIC_API_KEY`; `openai-compatible` without `OPS_AGENT_MODEL_BASE_URL` |
| Kubernetes | `observability.opsAgent` in the chart values (endpoints of your monitoring stack, `secrets.data.OPS_AGENT_WEBHOOK_SECRET`); the webhook URL is the `<release>-ops-agent` Service. With `networkPolicy.enabled`: ingress from `monitoringNamespace` only; egress to DNS, that namespace, HTTPS on public addresses, and `modelEndpoints` (an in-house model server's CIDR and port) |

## Grafana

| Item | Value |
|---|---|
| URL | `http://localhost:<GRAFANA_PORT>/grafana/` on the host |
| Through the edge | `https://<host>:<FRONTEND_HTTPS_PORT>/grafana/` when `.env` sets `EDGE_GRAFANA=grafana:3000` ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 6) |
| Source addresses | `EDGE_GRAFANA_ALLOW`: addresses or CIDR blocks, space- or comma-separated; others get `403`. Unset: any. An invalid entry stops the edge from starting |
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
| `ops-agent-incidents` | Incidents | `incidents_dashboard.json` | Counts per priority (P1 critical, P2 warning), all incidents, notes without a model; incidents over time by priority; incident table by priority (what happened, likely cause, next step); detail of the incident picked in the table, or the newest. Variables `alertname`, `severity`, `incident`. Refresh `1m` |
| `container_logs` | Container Logs Dashboard | `logs_dashboard.json` | Log rate by service, active containers, current log rate, top 10 services by volume, log viewer. Variables `service`, `container`, `search` (regex). Refresh `10s` |

## Metrics

| Source | Metrics |
|---|---|
| `api-gateway`, `ml-service-api`, `ingestion-service` (`prometheus-fastapi-instrumentator` defaults) | `http_requests_total{handler,method,status}` (`status` grouped: `2xx`, `5xx`, …), `http_request_duration_seconds{handler,method}`, `http_request_duration_highr_seconds`, `http_request_size_bytes`, `http_response_size_bytes` |
| `ml-service-api` | `ml_stateful_features_neutralized_total` — see [kill-switch](#runbook-stateful-feature-kill-switch); `ml_stateful_features_unavailable_total` — stateful slots served neutral because their aggregate was unavailable ([ADR-0044](../../ADR/ADR-0044-complete-model-input.md) dec 3) |
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
| Dead-lettered readings | `sum by (topic) (increase(kafka_topic_partition_current_offset{topic=~".+\\.dlq"}[15m]))` ([dead-letter handling](dead-letters.md)) |

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
| Platform rules, Compose | `infrastructure/prometheus/rules/`, evaluated by `prometheus`, routed by `alertmanager` (no receiver as shipped) |
| Platform rules, Kubernetes | Helm `PrometheusRule` ([platform alert rules](../../deploy/observability/README.md#platform-alert-rules)) |
| Tenant sensor and streaming alerts | `alert-service-detector`, tenant-isolated, never this stack ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 5) |

## Runbook: stateful-feature kill-switch

The switch ([ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)) makes the feature engine
fill every stateful transform's slot (`statistical`, `rolling_stat`) with its neutral value without
calling the transform, so inference stops reading the aggregate tables. Vector length and order are
unchanged. Use it when the database is alive but overloaded; in a total outage it reads as enabled
and each stateful slot whose aggregate read fails takes the same neutral value, counted in
`ml_stateful_features_unavailable_total` and named in `neutralized_features`.

| Task | Command / signal |
|---|---|
| Who | A platform superuser ([ADR-0043](../../ADR/ADR-0043-platform-switch-operator-control.md)); no tenant role |
| Turn off (neutralize) | `PUT /api/platform/switches/stateful_features_enabled` with `{"value": false}` |
| Turn back on | same with `{"value": true}` |
| State | `GET /api/platform/switches` |
| Who flipped it, when | `GET /api/platform/switches/stateful_features_enabled/changes` (`public.platform_config_changes`, append-only) |
| Takes effect | within `STATEFUL_SWITCH_CACHE_TTL` seconds (default `5`); no restart |
| Confirm, response | `POST /api/inference/predict` returns `degraded: true` and `neutralized_features` |
| Confirm, metric | `rate(ml_stateful_features_neutralized_total[5m])` > 0; `0` in normal operation |

`ml_service_user` holds `SELECT` only on `public.platform_config`; the service never writes the flag.
The gateway writes it only through `set_platform_switch()`, which records the flip; a flip to the
current value is not recorded.
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
| No incident note for a firing alert | The webhook is missing from `ALERTMANAGER_CONFIG`, its secret differs (`401` in `docker compose logs alertmanager`), or the `ops-agent` profile is not up | Add the receiver as in [Infrastructure agent](#infrastructure-agent); `docker compose --profile ops-agent up -d ops-agent` |
| Incident note reads "The model could not write this note" | The model key is missing or wrong, the endpoint is unreachable, or the model declined; the note's `error` field names it | Set `ANTHROPIC_API_KEY` or `OPS_AGENT_MODEL_BASE_URL` / `OPS_AGENT_MODEL_API_KEY`; `docker compose logs ops-agent` |

## Links

- [Prometheus](https://prometheus.io/docs/) · [PromQL](https://prometheus.io/docs/prometheus/latest/querying/basics/) · [Grafana](https://grafana.com/docs/) · [Loki](https://grafana.com/docs/loki/) · [LogQL](https://grafana.com/docs/loki/latest/query/)
