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
| `spark-build-info` | `spark-streaming:9108`, `spark-aggregations:9108` | `/metrics` |
| `alert-worker-build-info` | `alert-service-detector:9108` | `/metrics` |
| `spark-master` | `spark-master:8080` | `/metrics/master/prometheus` |
| `loki` | `loki:3100` | `/metrics`; `up{job="loki"}` is Loki's health signal (distroless image, no container healthcheck) |
| `promtail` | `promtail:9080` | `/metrics` |
| `alertmanager` | `alertmanager:9093` | `/metrics` |

## Alerts

| Rules file | Loaded by |
|---|---|
| [`platform.yml`](../../infrastructure/prometheus/rules/platform.yml) | Compose, and the chart's PrometheusRule ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 1) |
| [`compose.yml`](../../infrastructure/prometheus/rules/compose.yml) | Compose only: the backup and rehearsal jobs' and the device CA watch's success times, from node-exporter's textfile collector (`backup-metrics` and `device-ca-metrics` volumes). The chart raises the same alerts from its CronJobs ([chart rules](../../deploy/observability/README.md#platform-alert-rules)) |

| Alert | Fires when | Handling |
|---|---|---|
| `IndustryFlowTargetDown` | A scrape target is down for 5m | `docker compose ps`; the service's logs |
| `IndustryFlowPostgresDown` | `postgres-exporter` cannot reach TimescaleDB for 5m | `docker compose logs timescaledb` |
| `IndustryFlowRedisDown` | `redis-exporter` cannot reach Redis for 5m | `docker compose logs redis` |
| `IndustryFlowRedisMemoryHigh` | Redis above 80% of its `maxmemory` for 10m; it never evicts, so at the limit mints, sign-ins and cache writes fail | `redis-cli info memory`; `redis-cli --bigkeys`; the capability-use stream's backlog (`capability_audit_backlog`) |
| `IndustryFlowKafkaConsumerLag` | A consumer group lags more than 1000 for 10m | The consumer's logs |
| `IndustryFlowDeadLetteredReadings` | A letter reached a `*.dlq` topic in the last 15m | [Dead-letter handling](dead-letters.md) |
| `IndustryFlowTenantStatusStalled` | A `tenant-status` partition that carried statuses within the hour has carried none for 5m; ingestion has forgotten every suspension ([ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) dec 3) | `docker compose logs alert-service-detector` (`tenant-status publish failed`); `docker compose ps kafka`; [stream processing](../architecture/stream-processing.md) |
| `IndustryFlowCapabilityAuditBacklog` | More than 1000 capability-use events wait to be recorded for 15m | The API gateway's logs (`capability audit writer`); [capability-use record](../architecture/notebooks.md#capability-use-record) |
| `IndustryFlowBackupStale` | No `databases` or `notebooks` backup has completed for two days (Compose) | [Backup and disaster recovery](backup-and-recovery.md#running-backups); the job's output |
| `IndustryFlowBackupNeverCompleted` | No database backup has ever completed, for a day (Compose) | Run and schedule `db-backup`: [running backups](backup-and-recovery.md#running-backups) |
| `IndustryFlowRestoreRehearsalStale` | No restore rehearsal has passed for nine days (Compose) | [Restore rehearsal](backup-and-recovery.md#restore-rehearsal); its output names the table or object that differed |
| `IndustryFlowRestoreRehearsalNeverPassed` | No database restore rehearsal has ever passed, for a week (Compose) | Run and schedule `restore-rehearsal`: [restore rehearsal](backup-and-recovery.md#restore-rehearsal) |
| `IndustryFlowDeviceCrlStale` | No device CA `check` (CRLs and CA certificates) has passed for 13h, or none ever has; once one expires every device is refused (Compose; the chart raises it from `device-crl-check`) | `docker logs industryflow-device-crl-refresh` names it; [expiry watch](device-mtls.md#expiry-watch) |
| `IndustryFlowDeviceCertExpiring` | No `check-certs` has passed for 13h, or none ever has: a device's renewal is owed (Compose; the chart raises it from `device-cert-check`) | `docker logs industryflow-device-crl-refresh` names the device; renew it ([expiry watch](device-mtls.md#expiry-watch)) |
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
| Evidence | The rule's PromQL over the 30 minutes before the alert to now; the revisions every service reported over the same window, in the order they ran (`industryflow_build_info`, each point the last value in its step, a change marked `CHANGED`, revisions cut to 12 characters); error lines of the services in the rule's `services` annotation and the target's host; other active alerts; the alert's row in the [alert table](#alerts) (every platform rule has one); `Symptom \| Cause \| Fix` rows sharing at least two words with the alert. The metric series are narrowed to the alert's own labels |
| Sent to the model | The alert and the evidence, after redaction: tenant identifiers, emails, network addresses and credential-shaped strings become `<tenant-1>`, `<email-1>`, … ; the note records the counts per category |
| Output | Loki stream `{job="ops-agent"}`; the `complete-system` and Incidents dashboards draw it as annotations. No Grafana credential |
| Note fields | `alertname`, `severity`, `services`, `what`, `impact`, `cause`, `next_step` (the model's four sections), `text` (the whole note), `model`, `evidence`, `redacted`, `fingerprint`, `key` |
| Without a model | A model error or refusal still publishes a note: the evidence listing, `model` = `none` |
| Once per firing | A firing (fingerprint and start) is explained once; remembered 7 days, and after a restart the agent finds its earlier note in Loki |
| Alert storm | At most 5 alerts of one webhook (critical first) and 10 waiting; the rest are logged as not explained |
| Refuses to start | Webhook secret under 32 characters; `anthropic` without `ANTHROPIC_API_KEY`; `openai-compatible` without `OPS_AGENT_MODEL_BASE_URL` |
| Kubernetes | `observability.opsAgent` in the chart values (endpoints of your monitoring stack, `secrets.data.OPS_AGENT_WEBHOOK_SECRET`); the webhook URL is the `<release>-ops-agent` Service. With `networkPolicy.enabled` (the default): ingress from `observability.monitoringNamespace` only; egress to DNS, that namespace, HTTPS on public addresses, and `modelEndpoints` (an in-house model server's CIDR and port) |

## Grafana

| Item | Value |
|---|---|
| URL | `http://localhost:<GRAFANA_PORT>/grafana/` on the host |
| Through the edge | `https://<host>:<FRONTEND_HTTPS_PORT>/grafana/` when `.env` sets `EDGE_GRAFANA=grafana:3000` ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 6) |
| Source addresses | `EDGE_GRAFANA_ALLOW`: addresses or CIDR blocks, space- or comma-separated; others get `403`. Unset: any. An invalid entry stops the edge from starting |
| Sign-on through the edge | The platform session of a superuser; no session: `302` to `/login?next=<path>` (back to Grafana after sign-in), `401` on `/grafana/api/`; any other user: `403`; a write without `Sec-Fetch-Site: same-origin` from a browser: `403`. Grafana user = `if-<platform user id>`, role `Editor`, Grafana Live off ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 7) |
| Sign-on key | `dashboards-key-init` generates it once: private half in volume `industryflow-dashboards-signing-key` (gateway only), public half in `industryflow-dashboards-verify-key` (Grafana) |
| Rotate the sign-on key | `docker compose rm -sf dashboards-key-init grafana api-gateway && docker volume rm industryflow-dashboards-signing-key industryflow-dashboards-verify-key && docker compose up -d` |
| Login on the loopback URL | `GRAFANA_ADMIN_USER` / `GRAFANA_ADMIN_PASSWORD` from `.env` |
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
| `api-gateway`, `ml-service-api`, `ingestion-service`, `spark-streaming`, `spark-aggregations`, `alert-service-detector` | `industryflow_build_info{component,revision} 1`: the commit the image was built from (`INDUSTRYFLOW_REVISION` build argument; `unknown` when the build passed none); `component` is the compose service name, since a Kubernetes scrape sets its own `service` label; the Spark drivers serve it alone on port `9108` (`INDUSTRYFLOW_METRICS_PORT`), apart from the `PrometheusServlet`, and `alert-service-detector`, which serves no other metrics, on the same port; `spark-master` (stock image) and `spark-worker` report none ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 8) |
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
| Revision each service runs | `max by (component, revision) (industryflow_build_info)` |
| Revisions each service ran in the last day (more than one: deployed) | `count by (component) (max by (component, revision) (count_over_time(industryflow_build_info[1d])))` |
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
| `docker` | Docker socket: containers named `industryflow-*` (every service's `container_name`, `ingestion-service` and the no-network jobs included), and containers on `industryflow-network`, `industryflow-notebook-kernels` or `industryflow-ops-agent` (spawned kernels, `spark-worker`); one target per container | `job="docker"`, `container`, `container_id`, `service`, `project`, `level` (from JSON logs), `environment="production"`, `cluster="industryflow"` |
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
| No logs in Loki | Container neither named `industryflow-*` nor on `industryflow-network`, `industryflow-notebook-kernels` or `industryflow-ops-agent`, or promtail down (`docker logs industryflow-promtail`) | Give the service a `container_name` `industryflow-<service>`; `curl -s localhost:3100/ready` |
| `pg_up` is `0` | `metrics_user` has no password, or the CA mount for `verify-full` is missing (`docker logs industryflow-postgres-exporter`) | Set the password as `12-metrics-role.sql` describes; check the CA mount |
| `redis_up` is `0` | Redis down (`docker exec industryflow-redis redis-cli ping`) | Start `redis`, then the exporter |
| LogQL parse error | Unquoted label value or range without a unit | `{job="docker"}`, `[5m]` |
| No incident note for a firing alert | The webhook is missing from `ALERTMANAGER_CONFIG`, its secret differs (`401` in `docker compose logs alertmanager`), or the `ops-agent` profile is not up | Add the receiver as in [Infrastructure agent](#infrastructure-agent); `docker compose --profile ops-agent up -d ops-agent` |
| Incident note reads "The model could not write this note" | The model key is missing or wrong, the endpoint is unreachable, or the model declined; the note's `error` field names it | Set `ANTHROPIC_API_KEY` or `OPS_AGENT_MODEL_BASE_URL` / `OPS_AGENT_MODEL_API_KEY`; `docker compose logs ops-agent` |

## ADR-0016 dec 1, 2

The chart renders scrape targets, platform rules and dashboards as integration objects.

| Code | Role |
|---|---|
| `infrastructure/prometheus/rules/platform.yml` | Platform alert rules |
| `deploy/helm/industryflow/templates` | Observability templates (`observability-*.yaml`) |

## ADR-0016 dec 3

| Lane | Exporter login | TLS |
|---|---|---|
| Compose | `metrics_user` with `METRICS_DB_PASSWORD` | `verify-full` |
| Kubernetes | `metrics_user` with `METRICS_DB_PASSWORD` | `verify-full` |

`metrics_user` holds `pg_monitor` and a connection limit of 5.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/12-metrics-role.sql` | The monitoring role |
| `docker-compose.yml` | Compose exporter login |
| `deploy/helm/industryflow/templates/observability-exporters.yaml` | Kubernetes exporter login |
| `.env.example` | `METRICS_DB_PASSWORD` template |
| `tests/config_guards/test_compose_db_roles.py::test_postgres_exporter_uses_the_monitoring_role_over_verified_tls` | Proof: no superuser login, TLS verified |

## ADR-0016 dec 4

| Lane | Backend | Alert router |
|---|---|---|
| Compose | Prometheus, Loki, Grafana beside the services | `alertmanager`, one receiver `operator` with no delivery; `ALERTMANAGER_CONFIG` names the operator's copy |
| Kubernetes | The operator's stack; the chart renders `ServiceMonitor`, `PrometheusRule` and dashboard ConfigMaps | The operator's; `observability.*.labels` match its selectors |
| Kubernetes scrape of ingestion | The `metrics-listener` container in the ingestion pod serves only `/metrics` (from the app on loopback) on port `metrics` (`apps.ingestion-service.metricsListener.port`); its ServiceMonitor scrapes that port, and the NetworkPolicy admits `observability.prometheusPodLabels` pods in `observability.monitoringNamespace` to it alone | — |

| Code | Role |
|---|---|
| `infrastructure/prometheus/prometheus.yml` | Firing alerts sent to `alertmanager` |
| `infrastructure/alertmanager/alertmanager.yml` | Receiver with no delivery as shipped |
| `docker-compose.yml` | Backend services beside the application |
| `deploy/helm/industryflow/values.yaml` | Integration-object toggles and selector labels |
| `deploy/helm/industryflow/files/prometheus-rules.yml` | Platform rules rendered into the `PrometheusRule` |
| `deploy/helm/industryflow/templates/ops-agent.yaml` | Agent endpoints are the operator's stack |
| `deploy/helm/industryflow/templates/apps.yaml` | Ingestion's metrics-only listener |
| `tests/config_guards/test_helm_ingestion_edge_policy.py` | Proof: the listener serves only `/metrics`, is the port scraped, and the only port the monitoring stack reaches |
| `tests/config_guards/test_alert_rules.py::test_the_chart_renders_the_same_platform_rules` | Proof: one rule set in both lanes |

## ADR-0016 dec 5

| Path | Holds |
|---|---|
| Alert worker to the tenant's schema | Tenant alerts ([Alerts](#alerts-1)) |
| Prometheus and the platform rules | Operational, aggregate series; no tenant value or alert content |
| Grafana datasources | Prometheus and Loki; no database datasource, and the earlier `TimescaleDB` datasource is deleted at start |

| Code | Role |
|---|---|
| `services/alert_service/worker/repository.py` | Alert written to the tenant's store |
| `infrastructure/prometheus/rules/platform.yml` | Aggregate platform rules |
| `infrastructure/grafana/provisioning/datasources.yml` | Datasource set; database datasource deleted |
| `tests/config_guards/test_grafana_datasources.py::test_no_database_datasource_is_provisioned` | Proof: no database datasource |
| `tests/config_guards/test_compose_db_roles.py::test_grafana_has_no_database_datasource` | Proof: Grafana holds no database password |

## ADR-0016 dec 6, 7

Grafana is served at the edge and signs a platform superuser on from the platform session (see Grafana).

| Code | Role |
|---|---|
| `services/frontend/grafana.conf.template` | Grafana at the edge |
| `services/api_gateway/routers/dashboards_assertion.py` | Sign-on assertion from the platform session |

## ADR-0016 dec 8

Every service exposes its build information.

| Code | Role |
|---|---|
| `tests/config_guards/test_build_info.py` | Every image exposes build information |

## ADR-0024 dec 1

`register_transform` takes `stateful` (default false) and `neutral` (default 0.0); extension API 0.2.0 adds both. The engine asks `is_stateful` by type, so every statistic of a stateful type is governed together.

| Type | Stateful |
|---|---|
| `statistical` | Yes |
| `rolling_stat` | Yes |
| `identity`, `polynomial`, `interaction`, `deviation` | No |
| Unregistered | No |

| Code | Role |
|---|---|
| `services/ml_service/api/extensions/__init__.py::register_transform` | Class tag at registration |
| `services/ml_service/api/extensions/__init__.py::is_stateful` | Class lookup by type |
| `services/ml_service/api/extensions/builtins.py` | Built-in transforms and their tags |
| `services/ml_service/tests/test_stateful_kill_switch.py::test_stateful_transforms_are_tagged` | Proof: stateful types tagged |
| `services/ml_service/tests/test_stateful_kill_switch.py::test_unknown_type_is_not_stateful` | Proof: unknown type not stateful |

## ADR-0024 dec 2, 7

The engine reads the switch from the shared schema and keeps the last-known-good value when the read fails (see Runbook: stateful-feature kill-switch).

| Code | Role |
|---|---|
| `services/ml_service/api/feature_engineering/kill_switch.py` | Switch read, last-known-good |

## ADR-0024 dec 3-5

With the switch on, a stateful feature is null in its slot, checked before the substrate call, and counted.

| Code | Role |
|---|---|
| `services/ml_service/api/feature_engineering/engine.py` | Null-in-slot and degraded-serving metric |

## ADR-0024 dec 6

One row, `public.platform_config` key `stateful_features_enabled`, holds the switch for every tenant. It starts `true`; `ml_service_user` has `SELECT` only, so no service changes it.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/17-stateful-feature-kill-switch.sql` | The global row; read-only grant to `ml-service-api` |
| `services/ml_service/api/feature_engineering/kill_switch.py` | Reads the switch; never writes it |

## ADR-0043 dec 1, 2

The gateway serves listing, flipping and history of a switch; every route takes the superuser gate. `ml-service-api` only reads the switch.

| Code | Role |
|---|---|
| `services/api_gateway/routers/platform_switches.py` | Switch routes behind `require_superuser` |
| `services/api_gateway/dependencies.py::require_superuser` | No tenant role passes |
| `services/api_gateway/tests/test_platform_switches.py::test_no_tenant_role_passes` | Proof: tenant roles refused |
| `services/api_gateway/tests/test_platform_switches.py::test_every_route_takes_the_superuser_gate` | Proof: every route gated |

## ADR-0043 dec 3, 4

The gateway flips a switch through one privileged function that records the flip (see Runbook: stateful-feature kill-switch).

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/17-stateful-feature-kill-switch.sql` | The switch |
| `infrastructure/timescaledb/init-scripts/32-platform-switch-control.sql` | Flip function and flip record |

## ADR-0045 dec 1-6, 8

An agent is its own service: a trigger reaches its graph, deterministic steps gather and redact the evidence, a configured model writes the note from it, and the note says it is generated and names the model. It runs only when the operator enables it (see Infrastructure agent).

| Code | Role |
|---|---|
| `services/ops_agent/app.py` | Trigger receiver |
| `services/ops_agent/graph.py` | Gather, correlate, redact, explain, publish; the generated-note header |
| `services/ops_agent/tools.py` | Read-only tools over MCP |
| `services/ops_agent/model.py` | Model over an API, chosen by configuration |
| `services/ops_agent/redaction.py` | Redaction before the model |
| `docker-compose.yml` | `ops-agent` profile, off unless enabled |

## ADR-0046 dec 1

The operator adds a receiver for the agent to the alert router (see Infrastructure agent).

| Code | Role |
|---|---|
| `infrastructure/alertmanager/alertmanager.yml` | Alert router receivers |

## ADR-0046 dec 2

| Evidence | Tool | Source |
|---|---|---|
| The rule's PromQL around the alert | `prometheus_range` | Metrics store |
| Revisions the services reported | `prometheus_range` on `industryflow_build_info` | Metrics store |
| Error lines of the rule's `services` and the target's host | `loki_errors` | Log store |
| Active alerts | `active_alerts` | Alert router |
| The alert's row; matching symptom rows | `alert_runbook`, `runbook_rows` | Runbooks in the image |

| Code | Role |
|---|---|
| `services/ops_agent/tools.py::build_server` | The tool set |
| `services/ops_agent/graph.py::services_of` | Services whose logs are read |
| `services/ops_agent/graph.py::revisions_query` | Revision series over the window |
| `infrastructure/prometheus/rules/platform.yml` | `services` annotation on every rule |
| `tests/config_guards/test_rule_services.py` | Proof: every rule names real services |
| `services/ops_agent/tests/test_tools.py::test_the_tool_set_is_the_monitoring_plane` | Proof: tools reach the monitoring plane only |
| `infrastructure/promtail/promtail-config.yml` | Every service's logs collected, by container name or network |
| `tests/config_guards/test_logs_collected.py` | Proof: no Compose service is skipped by the collector |

## ADR-0046 dec 4

The publisher pushes each note to Loki under `job="ops-agent"`, labelled with `alertname` and `severity`, and reads that stream to skip a firing it has already explained.

| Code | Role |
|---|---|
| `services/ops_agent/publish.py::LokiPublisher` | Push and lookup on the note stream |
| `deploy/helm/industryflow/files/grafana/incidents_dashboard.json` | Incidents dashboard over the stream |
| `services/ops_agent/tests/test_tools.py::test_a_note_is_pushed_to_its_own_log_stream` | Proof: one stream |

## ADR-0046 dec 7

| Reach | Mechanism |
|---|---|
| Tool subprocess | Gets only the endpoint and path variables, never a secret |
| Credentials | `OPS_AGENT_WEBHOOK_SECRET`, `OPS_AGENT_MODEL_API_KEY`, `ANTHROPIC_API_KEY` |
| Kubernetes network | NetworkPolicy: ingress from `observability.monitoringNamespace`; egress to DNS, that namespace, HTTPS on public addresses and `modelEndpoints` |
| Compose network | `industryflow-ops-agent`, its only network, shared with `prometheus`, `loki` and `alertmanager` (which stay on `industryflow-network` too); not `internal`, for a hosted model endpoint |

| Code | Role |
|---|---|
| `services/ops_agent/app.py::McpTools` | Tool environment without secrets |
| `services/ops_agent/tools.py` | Metrics store, log store, alert router only |
| `deploy/helm/industryflow/templates/ops-agent.yaml` | Scoped secret and NetworkPolicy |
| `docker-compose.yml` | Compose service and its network |
| `tests/config_guards/test_ops_agent_network.py` | Proof: the agent shares a network only with the monitoring stack |

## ADR-0046 dec 8

| Category | Placeholder |
|---|---|
| Tenant identifiers (UUIDs, also inside `tenant_<uuid>` names, and 32-hex ids) | `<tenant-N>` |
| E-mail addresses | `<email-N>` |
| Network addresses, confirmed by the address parser | `<address-N>` |
| Credential-shaped strings (bearer and basic values, JWTs, known key prefixes, URL passwords, values of credential-named keys, long hex) | `<secret-N>` |

One `Redactor` serves one note, so a value keeps its placeholder within it; the note records counts per category.

| Code | Role |
|---|---|
| `services/ops_agent/redaction.py::Redactor` | Replacement and per-note placeholders |
| `services/ops_agent/tests/test_redaction.py::test_each_category_is_replaced` | Proof: every category |
| `services/ops_agent/tests/test_redaction.py::test_a_value_keeps_one_placeholder_within_a_note` | Proof: stable placeholders |
| `services/ops_agent/tests/test_redaction.py::test_the_record_holds_counts_never_values` | Proof: counts only |

## ADR-0046 dec 9

| Table | Header | Read |
|---|---|---|
| Alert table | `Alert \| Fires when \| Handling` | By the alert's exact name |
| Symptom tables | `Symptom \| Cause \| Fix` | Rows sharing at least two non-stop words with the alert, best three |

The image copies `docs/operations` as the runbooks; CI runs the agent's tests on any change there.

| Code | Role |
|---|---|
| `services/ops_agent/runbooks.py::load_alerts` | Alert table by name |
| `services/ops_agent/runbooks.py::match` | Symptom rows by shared words |
| `services/ops_agent/Dockerfile` | Runbooks in the image |
| `.github/workflows/unit-tests.yml` | Tests run on a runbook change |
| `services/ops_agent/tests/test_runbooks.py::test_every_symptom_table_keeps_the_contract_shape` | Proof: table shape |
| `services/ops_agent/tests/test_runbooks.py::test_every_platform_rule_has_a_row_in_the_alert_table` | Proof: alert table complete |

## ADR-0046 dec 10

The endpoints default to the Compose service names and are set per deployment; both lanes ship the agent off.

| Lane | Off by default | Endpoints |
|---|---|---|
| Compose | Profile `ops-agent` | `prometheus`, `loki`, `alertmanager` |
| Kubernetes | `observability.opsAgent.enabled: false` | `prometheusUrl`, `lokiUrl`, `alertmanagerUrl`, required when enabled |

| Code | Role |
|---|---|
| `services/ops_agent/config.py::Config` | Endpoints from the environment |
| `deploy/helm/industryflow/templates/ops-agent.yaml` | Refuses to render without the operator's endpoints |
| `deploy/helm/industryflow/templates/observability-dashboards.yaml` | Incidents dashboard as a ConfigMap |

## Links

- [Prometheus](https://prometheus.io/docs/) · [PromQL](https://prometheus.io/docs/prometheus/latest/querying/basics/) · [Grafana](https://grafana.com/docs/) · [Loki](https://grafana.com/docs/loki/) · [LogQL](https://grafana.com/docs/loki/latest/query/)
