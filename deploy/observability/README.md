<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Cluster monitoring stack

The manual of [ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md): the Kubernetes monitoring backend and the integration objects the Helm chart emits for it.

## ADR-0016 dec 1, 2

| Code | Role |
|---|---|
| `deploy/helm/industryflow/templates/observability-servicemonitors.yaml` | ServiceMonitors and the `platform="industryflow"` target label |
| `deploy/helm/industryflow/templates/observability-exporters.yaml` | Data-store exporters |
| `deploy/helm/industryflow/templates/observability-prometheusrules.yaml` | The `PrometheusRule` |
| `deploy/helm/industryflow/templates/observability-dashboards.yaml` | Dashboard ConfigMaps |

### Chart values

All under `observability:` in `deploy/helm/industryflow/values.yaml`.

| Key | Default | Meaning |
|---|---|---|
| `serviceMonitors.enabled` | `false` | Render ServiceMonitors; needs the Operator CRDs |
| `serviceMonitors.labels` | `{}` | Labels matched by the Operator's `serviceMonitorSelector` |
| `serviceMonitors.interval` / `scrapeTimeout` | `30s` / `10s` | Per-endpoint scrape settings |
| `prometheusRules.enabled` | `false` | Render the `PrometheusRule`; needs the Operator CRDs |
| `prometheusRules.labels` | `{}` | Labels matched by the Operator's `ruleSelector` |
| `dashboards.enabled` | `true` | Render dashboard ConfigMaps |
| `dashboards.label` | `grafana_dashboard` | Label the Grafana sidecar selects on (value `"1"`) |
| `exporters.{postgres,redis,kafka}.enabled` | `true` | Deploy the data-store exporter and its ServiceMonitor |

### Objects the chart emits

| Object | Rendered for | Template |
|---|---|---|
| ServiceMonitor per app | each `apps.<name>` with `enabled` and `metrics: true`: `api-gateway`, `ml-service-api`, `ingestion-service`, `spark-streaming`, `spark-aggregations`, `alert-service-detector` (`ingestion-service` on its metrics-only listener, port `metrics`; Spark on `metricsPort` `4040`, paths `/metrics/prometheus` and `/metrics/executors/prometheus`; the Spark drivers' and the alert worker's revision on `buildInfoPort` `9108`, path `/metrics`) | `observability-servicemonitors.yaml` |
| ServiceMonitor per exporter | each enabled exporter, port `metrics` | `observability-servicemonitors.yaml` |
| Target label `platform="industryflow"` | relabeling on every ServiceMonitor endpoint; every platform rule selects on it | `observability-servicemonitors.yaml` |
| Exporters | `postgres-exporter` (`9187`), `redis-exporter` (`9121`), `kafka-exporter` (`9308`) | `observability-exporters.yaml` |
| PrometheusRule `<fullname>-platform` | group `industryflow.platform` from `files/prometheus-rules.yml` (symlink to `infrastructure/prometheus/rules/platform.yml`, the file Compose loads); group `industryflow.kubernetes` | `observability-prometheusrules.yaml` |
| Dashboard ConfigMaps | each `files/grafana/*.json` (symlink to `infrastructure/grafana/provisioning/`) | `observability-dashboards.yaml` |

node-exporter, kube-state-metrics and cAdvisor come from the backend, not the chart.

### Platform alert rules

Group `industryflow.platform`: [`infrastructure/prometheus/rules/platform.yml`](../../infrastructure/prometheus/rules/platform.yml)
(`IndustryFlowTargetDown`, `IndustryFlowPostgresDown`, `IndustryFlowRedisDown`, `IndustryFlowRedisMemoryHigh`,
`IndustryFlowKafkaConsumerLag`, `IndustryFlowTenantStatusStalled`, `IndustryFlowDeadLetteredReadings`,
`IndustryFlowCapabilityAuditBacklog`, `IndustryFlowCapabilityEventsDropped`), the same rules and thresholds
as Compose. Tune them in that file, or add a PrometheusRule of your own. Unit tests:
`infrastructure/prometheus/rules/tests/platform_test.yml` (`promtool test rules`).

Group `industryflow.kubernetes`, on kube-state-metrics series:

| Alert | Expression | `for` | Severity |
|---|---|---|---|
| `IndustryFlowDeviceCrlStale` | `time() - (kube_cronjob_status_last_successful_time{cronjob="<fullname>-device-crl-check"} or on (namespace, cronjob) kube_cronjob_created{…}) > ingress.mtls.crlCheck.alertAfterSeconds`; rendered only when `ingress.enabled`, `ingress.mtls.enabled` and `ingress.mtls.crlCheck.enabled` | none | critical |
| `IndustryFlowDeviceCertExpiring` | `time() - (kube_cronjob_status_last_successful_time{cronjob="<fullname>-device-cert-check"} or on (namespace, cronjob) kube_cronjob_created{…}) > ingress.mtls.certCheck.alertAfterSeconds`; rendered only when `ingress.enabled`, `ingress.mtls.enabled` and `ingress.mtls.certCheck.enabled` | none | warning |
| `IndustryFlowBackupStale` | `time() - (kube_cronjob_status_last_successful_time{cronjob="<fullname>-db-backup"} or on (namespace, cronjob) kube_cronjob_created{…}) > backup.alertAfterSeconds`; rendered when `backup.enabled` | none | critical |
| `IndustryFlowRestoreRehearsalStale` | `time() - (kube_cronjob_status_last_successful_time{cronjob="<fullname>-restore-rehearsal"} or on (namespace, cronjob) kube_cronjob_created{…}) > backup.rehearsal.alertAfterSeconds`; rendered when `backup.enabled` and `backup.rehearsal.enabled` | none | warning |

Dead letters: [dead-letter handling](../../docs/operations/dead-letters.md). Device CRL:
[device mTLS](../../docs/operations/device-mtls.md#kubernetes). Backups and rehearsals:
[backup and disaster recovery](../../docs/operations/backup-and-recovery.md). Unit tests:
`deploy/observability/tests/kubernetes_rules_test.yml`.

## ADR-0016 dec 3

`postgres-exporter` connects as `metrics_user` (`pg_monitor`) with `METRICS_DB_PASSWORD` from its
workload Secret, over `hostssl` with `tls.db.sslmode` when `tls.db.enabled`
([ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md)).

| Code | Role |
|---|---|
| `deploy/helm/industryflow/templates/observability-exporters.yaml` | Exporter login as `metrics_user` |

## ADR-0016 dec 4

The backend is installed beside the chart; the `labels` values of the integration objects match its selectors.

### Install the backend (self-hosted)

| Component | Command |
|---|---|
| Prometheus Operator, Prometheus, Grafana, Alertmanager, node-exporter, kube-state-metrics | `helm repo add prometheus-community https://prometheus-community.github.io/helm-charts` then `helm install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace` |
| Loki and a log collector | `helm repo add grafana https://grafana.github.io/helm-charts` then `helm install loki grafana/loki-stack -n monitoring` |

A managed deployment ([ADR-0001](../../ADR/ADR-0001-industryflow-framing.md)) points the same
objects at the provider's stack instead.

### Matching the stack's selectors

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

## ADR-0016 dec 5

Tenant sensor and streaming alerts never pass through this stack ([alert paths](../../docs/operations/monitoring.md#adr-0016-dec-5)).

Links: [monitoring](../../docs/operations/monitoring.md) (Compose stack, metric names, dashboards, runbooks).
