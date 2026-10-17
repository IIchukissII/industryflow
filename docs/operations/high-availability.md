<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# High availability and resilience

Disruption budgets, spreading and autoscaling of the stateless tier in the Helm chart, and the
single instances of the stateful backends. Packaging and the deferred stateful-operator decision:
[ADR-0009](../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md).

## Stateless tier (Helm)

| Mechanism | Values | Behaviour |
|---|---|---|
| PodDisruptionBudget | `resilience.podDisruptionBudgets.enabled` (`true`), `maxUnavailable` (`1`) | Rendered for each app with more than one replica or HPA on |
| Pod anti-affinity | `resilience.antiAffinity.enabled` (`true`), `type` (`soft` / `hard`), `topologyKey` (`kubernetes.io/hostname`) | Same apps; `soft` = preferred, `hard` = required (one replica per node) |
| HorizontalPodAutoscaler | `apps.<name>.hpa.enabled` (`false`), `minReplicas`, `maxReplicas`, `targetCPUUtilizationPercentage` | `autoscaling/v2`, CPU; the Deployment then omits `replicas`. Requires metrics-server. Defined for `api-gateway`, `ml-service-api`, `frontend` |

| App | Default `replicas` |
|---|---|
| `api-gateway`, `ingestion-service`, `frontend` | `2` |
| `ml-service-api`, `alert-service-detector`, `spark-streaming`, `spark-aggregations` | `1` |

```yaml
resilience:
  podDisruptionBudgets: { enabled: true, maxUnavailable: 1 }
  antiAffinity: { enabled: true, type: soft }
apps:
  api-gateway:
    hpa: { enabled: true, minReplicas: 2, maxReplicas: 6, targetCPUUtilizationPercentage: 70 }
```

Kafka consumers (alert detector, Spark jobs) scale by partition lag, not CPU. The chart ships no
lag-based autoscaler; KEDA on `kafka_consumergroup_lag` (kafka-exporter,
[ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md)) is a cluster add-on.
Consumer parallelism is bounded by the topic's partition count.

## Stateful backends

Each runs as one instance in both Compose and the chart.

| Backend | Instance | HA option |
|---|---|---|
| TimescaleDB | Single primary | Postgres operator with streaming replication, or managed Postgres. Backups: [backup and recovery](backup-and-recovery.md) |
| Kafka + ZooKeeper | Single broker, single ZooKeeper | Kafka operator or managed Kafka; replication factor ≥ 3, `min.insync.replicas` ≥ 2 |
| Redis | Single | Sentinel / Cluster, an operator, or managed |
| MinIO | Single | Distributed MinIO with erasure coding, or managed S3 |
| Spark (Compose) | Master + worker pool (`SPARK_WORKER_REPLICAS` or `--scale spark-worker=N`) | Shared S3A checkpoint ([checkpoints](../architecture/stream-processing.md#checkpoints)) |
| Spark (Helm) | One driver per job, local mode | Checkpoint on the object store ([checkpoints](../architecture/stream-processing.md#checkpoints)) |
