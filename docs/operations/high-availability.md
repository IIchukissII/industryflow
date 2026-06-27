<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# High availability & resilience

How the platform survives node drains, upgrades, and load — and the path to removing the
remaining single points of failure. The **stateless** tier is handled by the Helm chart; the
**stateful** backends are a deployment choice (operators or managed services), consistent with
ADR-0009's deferred "stateful operators" decision.

## Stateless app tier (in the chart)

Configured under `resilience:` and per-app `apps.<name>.hpa` (values.yaml):

- **PodDisruptionBudgets** — generated for every app with more than one replica (or HPA on),
  `maxUnavailable: 1` by default, so a node drain/upgrade never takes all replicas at once.
  `maxUnavailable` (not `minAvailable`) keeps drain working even at one replica.
- **Pod anti-affinity** — `soft` by default (preferred; won't block scheduling on a small
  cluster), `hard` for one-replica-per-node (`resilience.antiAffinity.type`). Spreads replicas
  across nodes (`topologyKey: kubernetes.io/hostname`).
- **Horizontal Pod Autoscaling** — opt-in per app (`apps.<name>.hpa.enabled`), CPU-based
  (`autoscaling/v2`). When enabled, the Deployment omits `replicas` so the HPA owns the count.
  Requires **metrics-server** in the cluster. Provided for the HTTP services (api-gateway,
  ml-service-api, frontend).

```yaml
resilience:
  podDisruptionBudgets: { enabled: true, maxUnavailable: 1 }
  antiAffinity: { enabled: true, type: soft }      # or "hard"
apps:
  api-gateway:
    hpa: { enabled: true, minReplicas: 2, maxReplicas: 6, targetCPUUtilizationPercentage: 70 }
```

### Autoscaling Kafka consumers (lag-based)

CPU HPA does not fit Kafka consumers — they scale by **partition lag**, not CPU. For the
alert detector and the Spark consumers, drive scaling from lag with **KEDA**: a `ScaledObject`
on the `kafka_consumergroup_lag` series (exposed by the kafka-exporter, ADR-0016). Consumer
parallelism is still bounded by the topic's partition count, so size partitions first. KEDA is
a cluster add-on, out of scope for the app chart.

## Stateful backends — the SPOFs and their HA path

Each runs as a single instance today (one StatefulSet/Deployment). Production HA is an operator
or a managed service per backend:

| Backend | SPOF today | HA path |
|---------|-----------|---------|
| **TimescaleDB** | single primary | Streaming replication via a Postgres operator (CloudNativePG / Crunchy / Patroni), or managed Postgres. Backups already exist (tier-1 DR, `backup-and-recovery.md`); replication narrows RPO/RTO. |
| **Kafka + ZooKeeper** | single broker | The **Strimzi** operator (multi-broker, KRaft — drops ZooKeeper), or managed Kafka. Replication factor ≥ 3 + `min.insync.replicas` ≥ 2. |
| **Redis** | single | Redis Sentinel / Cluster, or a Redis operator, or managed. Redis here is a cache/feature store; loss is recoverable. |
| **MinIO** | single | Distributed MinIO (≥ 4 drives/nodes) with erasure coding, or managed S3 (the recommended off-site DR target anyway). |
| **Spark worker** | single worker | Scale workers (replicas) behind the master, or move to spark-on-k8s (ADR-0009 deferred) for per-job executors. |

Adopting these is a per-environment decision; the managed model (ADR-0001) typically uses the
cloud provider's HA equivalents instead of running the operators.

## See also

- `docs/operations/backup-and-recovery.md` — DR / restore (complements replication).
- `ADR/ADR-0009-...` — packaging; the stateful-operator decision deferred to per-environment.
