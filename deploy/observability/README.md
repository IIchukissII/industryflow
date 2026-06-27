<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Cluster monitoring stack

Observability is **cluster infrastructure**, deployed and operated separately from the
IndustryFlow application chart (ADR-0016). The app chart only emits the integration objects a
Prometheus-Operator-compatible stack discovers — **ServiceMonitors**, **PrometheusRules**, and
**Grafana-dashboard ConfigMaps** — plus the data-store exporters (postgres/redis/kafka). This
directory documents the backend those objects plug into.

> **Two alert planes (tenancy).** What lives here is the **system/platform** plane —
> operator-facing infra health. The **tenant streaming/sensor alerts** (the alert-service's
> per-tenant detections) are tenant data and stay in the tenant-isolated path and the tenant's
> own UI (ADR-0003); they are deliberately **never** routed through this shared stack.

## Install the backend (self-hosted)

```bash
# Prometheus Operator + Prometheus + Grafana + Alertmanager + node-exporter + kube-state-metrics
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install monitoring prometheus-community/kube-prometheus-stack -n monitoring --create-namespace

# Loki + the log collector (Grafana Alloy / promtail), for logs
helm repo add grafana https://grafana.github.io/helm-charts
helm install loki grafana/loki-stack -n monitoring
```

Managed deployments (ADR-0001) skip this and point at the provider's stack (Grafana Cloud,
AMP, Datadog, …); the same ServiceMonitors/rules/dashboards integrate with it.

## Turn the integration on in the app chart

The CR-emitting toggles default **off** (they need the Operator CRDs). Enable them once the
backend is installed, and set the labels the Operator selects on:

```yaml
# values overlay for the industryflow app chart
observability:
  serviceMonitors:
    enabled: true
    labels:
      release: monitoring        # match kube-prometheus-stack's serviceMonitorSelector
  prometheusRules:
    enabled: true
    labels:
      release: monitoring        # match its ruleSelector
  dashboards:
    enabled: true                # ConfigMaps the Grafana sidecar auto-imports
```

Check your stack's `serviceMonitorSelector` / `ruleSelector` (kube-prometheus-stack defaults to
matching `release: <name>`) and set `observability.*.labels` to match, otherwise the Operator
ignores the objects.

## What the app chart provides

| Object | Source | Notes |
|--------|--------|-------|
| ServiceMonitor (per app) | generated for any `apps.<name>.metrics: true` | api-gateway, alert-service-api, ml-service-api today |
| ServiceMonitor (per exporter) | postgres/redis/kafka exporters | `observability.exporters.*` |
| PrometheusRule | `observability.prometheusRules` | target-down, pg/redis down, Kafka lag |
| Grafana dashboards | `infrastructure/grafana/provisioning/*.json` | shipped as labelled ConfigMaps |
| postgres/redis/kafka exporters | `observability.exporters.*` | postgres-exporter uses the `metrics_user` (`pg_monitor`) role, not the superuser |

See `ADR/ADR-0016-observability-and-monitoring-integration.md` for the full rationale.
