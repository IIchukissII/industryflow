<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Cluster monitoring stack

The Kubernetes monitoring backend and the integration objects the Helm chart emits for it
([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md)). Compose stack, metric
names, dashboards and runbooks: [monitoring](../../docs/operations/monitoring.md). Tenant sensor and
streaming alerts never pass through this stack
([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md) dec 5).

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
| Target label `platform="industryflow"` | relabeling on every ServiceMonitor endpoint; every platform rule selects on it | `observability-servicemonitors.yaml` |
| Exporters | `postgres-exporter` (`9187`), `redis-exporter` (`9121`), `kafka-exporter` (`9308`) | `observability-exporters.yaml` |
| PrometheusRule `<fullname>-platform` | group `industryflow.platform` from `files/prometheus-rules.yml` (symlink to `infrastructure/prometheus/rules/platform.yml`, the file Compose loads); group `industryflow.kubernetes` | `observability-prometheusrules.yaml` |
| Dashboard ConfigMaps | each `files/grafana/*.json` (symlink to `infrastructure/grafana/provisioning/`) | `observability-dashboards.yaml` |

`postgres-exporter` connects as `metrics_user` (`pg_monitor`) with `METRICS_DB_PASSWORD` from its
workload Secret, over `hostssl` with `tls.db.sslmode` when `tls.db.enabled`
([ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md)). node-exporter,
kube-state-metrics and cAdvisor come from the backend, not the chart.

## Platform alert rules

Group `industryflow.platform`: [`infrastructure/prometheus/rules/platform.yml`](../../infrastructure/prometheus/rules/platform.yml)
(`IndustryFlowTargetDown`, `IndustryFlowPostgresDown`, `IndustryFlowRedisDown`,
`IndustryFlowKafkaConsumerLag`, `IndustryFlowDeadLetteredReadings`), the same rules and thresholds
as Compose. Tune them in that file, or add a PrometheusRule of your own. Unit tests:
`infrastructure/prometheus/rules/tests/platform_test.yml` (`promtool test rules`).

Group `industryflow.kubernetes`, on kube-state-metrics series:

| Alert | Expression | `for` | Severity |
|---|---|---|---|
| `IndustryFlowDeviceCrlStale` | `time() - kube_cronjob_status_last_successful_time{cronjob="<fullname>-device-crl-check"} > ingress.mtls.crlCheck.alertAfterSeconds`; rendered only when `ingress.enabled`, `ingress.mtls.enabled` and `ingress.mtls.crlCheck.enabled` | none | critical |
| `IndustryFlowDeviceCertExpiring` | `time() - kube_cronjob_status_last_successful_time{cronjob="<fullname>-device-cert-check"} > ingress.mtls.certCheck.alertAfterSeconds`; rendered only when `ingress.enabled`, `ingress.mtls.enabled` and `ingress.mtls.certCheck.enabled` | none | warning |

Dead letters: [dead-letter handling](../../docs/operations/dead-letters.md). Device CRL:
[device mTLS](../../docs/operations/device-mtls.md#kubernetes).
