<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Cluster monitoring stack

The Kubernetes monitoring backend and the integration objects the IndustryFlow Helm chart emits for
it. The compose stack, metric names, dashboards and runbooks are in
[docs/operations/monitoring.md](../../docs/operations/monitoring.md). The decision is
[ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md).

This stack is the operator-facing platform plane. Tenant sensor and streaming alerts stay on the
tenant-isolated path and never pass through it (ADR-0016 decision 5,
[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)).

## Install the backend (self-hosted)

| Component | Command |
|---|---|
| Prometheus Operator, Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics | `helm repo add prometheus-community https://prometheus-community.github.io/helm-charts` then `helm install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace` |
| Loki and a log collector | `helm repo add grafana https://grafana.github.io/helm-charts` then `helm install loki grafana/loki-stack -n monitoring` |

A managed deployment ([ADR-0001](../../ADR/ADR-0001-industryflow-framing.md)) points the same
objects at the provider's stack instead.

## Chart values

All under `observability:` in `deploy/helm/industryflow/values.yaml`.

| Key | Default | Meaning |
|---|---|---|
| `serviceMonitors.enabled` | `false` | Render ServiceMonitors; needs the Operator CRDs |
| `serviceMonitors.labels` | `{}` | Labels matched by the Operator's `serviceMonitorSelector` |
| `serviceMonitors.interval` / `scrapeTimeout` | `30s` / `10s` | Per-endpoint scrape settings |
| `prometheusRules.enabled` | `false` | Render the `PrometheusRule`; needs the Operator CRDs |
| `prometheusRules.labels` | `{}` | Labels matched by the Operator's `ruleSelector` |
| `prometheusRules.targetDownFor` | `5m` | `for:` of the down rules |
| `prometheusRules.kafkaConsumerLagThreshold` / `kafkaConsumerLagFor` | `1000` / `10m` | Lag rule threshold and duration |
| `prometheusRules.deadLetterWindow` | `15m` | Window of the dead-letter rule |
| `dashboards.enabled` | `true` | Render dashboard ConfigMaps |
| `dashboards.label` | `grafana_dashboard` | Label the Grafana sidecar selects on (value `"1"`) |
| `exporters.{postgres,redis,kafka}.enabled` | `true` | Deploy the data-store exporter and its ServiceMonitor |

Overlay for kube-prometheus-stack installed as release `monitoring` (it selects on
`release: <release-name>`):

```yaml
observability:
  serviceMonitors:
    enabled: true
    labels:
      release: monitoring
  prometheusRules:
    enabled: true
    labels:
      release: monitoring
```

## Objects the chart emits

| Object | Rendered for | Template |
|---|---|---|
| ServiceMonitor per app | each `apps.<name>` with `enabled` and `metrics: true`: `api-gateway`, `ml-service-api`, `ingestion-service`, `spark-streaming`, `spark-aggregations` (Spark on `metricsPort` `4040`, paths `/metrics/prometheus` and `/metrics/executors/prometheus`) | `observability-servicemonitors.yaml` |
| ServiceMonitor per exporter | each enabled exporter, port `metrics` | `observability-servicemonitors.yaml` |
| Exporters | `postgres-exporter` (`9187`), `redis-exporter` (`9121`), `kafka-exporter` (`9308`) | `observability-exporters.yaml` |
| PrometheusRule `<fullname>-platform` | group `industryflow.platform` | `observability-prometheusrules.yaml` |
| Dashboard ConfigMaps | each `files/grafana/*.json` (symlink to `infrastructure/grafana/provisioning/`) | `observability-dashboards.yaml` |

`postgres-exporter` connects as `metrics_user` (`pg_monitor`) with `METRICS_DB_PASSWORD` from its
workload Secret, over `hostssl` with `tls.db.sslmode` when `tls.db.enabled`
([ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md)). node-exporter,
kube-state-metrics and cAdvisor come from the backend, not the chart.

## Platform alert rules

| Alert | Expression | `for` | Severity |
|---|---|---|---|
| `IndustryFlowTargetDown` | `up{job=~".*industryflow.*"} == 0` | `targetDownFor` | critical |
| `IndustryFlowPostgresDown` | `pg_up == 0` | `targetDownFor` | critical |
| `IndustryFlowRedisDown` | `redis_up == 0` | `targetDownFor` | warning |
| `IndustryFlowKafkaConsumerLag` | `kafka_consumergroup_lag > kafkaConsumerLagThreshold` | `kafkaConsumerLagFor` | warning |
| `IndustryFlowDeadLetteredReadings` | `increase(kafka_topic_partition_current_offset{topic=~".+\\.dlq"}[deadLetterWindow]) > 0`, summed by topic | none | warning |
| `IndustryFlowDeviceCrlStale` | `time() - kube_cronjob_status_last_successful_time{cronjob="<fullname>-device-crl-check"} > ingress.mtls.crlCheck.alertAfterSeconds`; rendered only when `ingress.enabled`, `ingress.mtls.enabled` and `ingress.mtls.crlCheck.enabled` | none | critical |

Dead letters: [ADR-0005](../../ADR/ADR-0005-kafka-delivery-semantics.md). Device CRL:
[ADR-0007](../../ADR/ADR-0007-public-key-infrastructure.md),
[device-mtls.md](../../docs/operations/device-mtls.md).
