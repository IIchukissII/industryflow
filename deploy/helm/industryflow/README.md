<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# IndustryFlow Helm chart

Kubernetes packaging for IndustryFlow ([ADR-0009](../../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md)): templates, values, image pinning and pre-production steps.

## Templates

| Template | Renders | Gate |
|---|---|---|
| `apps.yaml` | Deployment (+ Service, PDB, HPA) per entry in `apps` | `apps.<name>.enabled` |
| `configmap.yaml` | Shared non-secret config from `config` | always |
| `secret.yaml` | One scoped Secret per workload, `<release>-<workload>-secrets` | `secrets.create`, no `existingSecret` |
| `infra-timescaledb.yaml` | TimescaleDB StatefulSet + Service | `infra.timescaledb.inCluster` |
| `configmap-db-init.yaml` | `files/db-init` scripts, mounted at `/docker-entrypoint-initdb.d` | `infra.timescaledb.inCluster` |
| `configmap-pg-hba.yaml` | Hardened `pg_hba.conf` + `postgresql.conf` (`files/db-config`) | `infra.timescaledb.inCluster` |
| `db-tls-certificate.yaml` | cert-manager issuers, internal CA, DB server cert | `tls.db.certManager.enabled` |
| `infra-kafka.yaml` | ZooKeeper + Kafka StatefulSets | `infra.zookeeper` / `infra.kafka.inCluster` |
| `infra-redis.yaml` | Redis StatefulSet | `infra.redis.inCluster` |
| `infra-minio.yaml` | MinIO StatefulSet | `infra.minio.inCluster` |
| `infra-mlflow.yaml` | MLflow Deployment | `infra.mlflow.inCluster` |
| `job-minio-init.yaml` | post-install/upgrade Job running `files/minio-init.sh` | `infra.minio.inCluster` |
| `cronjob-db-backup.yaml` | Logical DB backup CronJob | `backup.enabled` |
| `cronjob-cold-export.yaml` | Cold-layer export CronJob | `coldExport.enabled` |
| `cronjob-device-crl-check.yaml` | Device CRL age check CronJob | `ingress.enabled`, `ingress.mtls.enabled`, `ingress.mtls.crlCheck.enabled` |
| `ingress.yaml` | HTTPS Ingress; device mTLS Ingress | `ingress.enabled`; mTLS also `ingress.mtls.enabled` |
| `networkpolicy.yaml` | NetworkPolicies | `networkPolicy.enabled` |
| `notebook-hub.yaml` | Hub, configurable-http-proxy, SSO proxy, RBAC, notebooks Ingress | `notebookHub.enabled` |
| `notebook-sql-proxy.yaml` | Notebook SQL proxy | `notebookHub.enabled` |
| `notebook-tracking-gateway.yaml` | Experiment-tracking gateway | `notebookHub.enabled` |
| `notebook-cold-broker.yaml` | Cold-store broker | `notebookHub.enabled` and `coldExport.enabled` |
| `observability-servicemonitors.yaml` | ServiceMonitors for apps with `metrics: true` | `observability.serviceMonitors.enabled` |
| `observability-prometheusrules.yaml` | PrometheusRules | `observability.prometheusRules.enabled` |
| `observability-dashboards.yaml` | Grafana dashboard ConfigMaps (`files/grafana`) | `observability.dashboards.enabled` |
| `observability-exporters.yaml` | postgres / redis / kafka exporters | `observability.exporters.<name>.enabled` |

`files/` holds symlinks to the compose sources; Helm follows them when it loads the chart.

| Symlink | Target |
|---|---|
| `files/db-init` | `infrastructure/timescaledb/init-scripts` |
| `files/db-config` | `infrastructure/timescaledb/config` |
| `files/minio-init.sh` | `infrastructure/minio/minio-init.sh` |
| `files/device-ca.sh` | `scripts/device-ca.sh` |
| `files/grafana` | `infrastructure/grafana/provisioning` |

## In-cluster vs external datastores

Each stateful dependency under `infra` has an `inCluster` toggle (ADR-0009 decision 3).

| `inCluster` | Result |
|---|---|
| `true` (default) | The chart renders the workload + Service |
| `false` | Nothing is rendered; config points at `infra.<dep>.external.*`, and rendering fails if that value is empty |

| Dependency | External key |
|---|---|
| `timescaledb` | `external.host`, `external.port` |
| `kafka` | `external.bootstrap` |
| `redis` | `external.host`, `external.port` |
| `minio` | `external.endpoint` |
| `mlflow` | `external.trackingUri` |

## Commands

| Task | Command |
|---|---|
| Render locally | `helm template if deploy/helm/industryflow --set secrets.failOnPlaceholders=false` |
| Lint | `helm lint deploy/helm/industryflow --set secrets.failOnPlaceholders=false` |
| Generate the digest overlay | `scripts/pin-image-digests.sh <tag> > deploy/helm/industryflow/values-digests.yaml` |
| Install | `helm install industryflow deploy/helm/industryflow --namespace industryflow --create-namespace -f deploy/helm/industryflow/values-digests.yaml -f <secrets-values>` |

## Image digest pinning

| Image kind | Pinning |
|---|---|
| Third-party (TimescaleDB, Kafka, ZooKeeper, Redis, MinIO, mc, exporters, notebook proxies, init images) | `name:tag@sha256:…` inline in `values.yaml` |
| First-party `industryflow-*` | Per-image `digest:` in every mapping with `image` + `digest`; empty uses `image.tag`, a set digest wins over the tag |

`scripts/pin-image-digests.sh [TAG]` derives the image list from `values.yaml` and resolves each
digest from GHCR over the registry API (`GITHUB_TOKEN` or `gh auth token` for private packages).

| Env var | Default | Meaning |
|---|---|---|
| `REGISTRY` | `image.registry` | Registry/namespace prefix |
| `GITHUB_TOKEN` | `gh auth token` | Token for private packages |

## Before production

| Item | Keys | Requirement |
|---|---|---|
| Secrets | `secrets.data`, `secrets.create`, `secrets.existingSecret` | Replace every `CHANGE_ME` from a secrets backend, or `secrets.create=false` + `existingSecret` (every workload then references that one Secret). Rendering fails on a placeholder while `secrets.failOnPlaceholders=true` |
| DB roles | `secrets.data.*_DB_PASSWORD`, `config.*_DB_USER` | Each service connects as its own role; the init scripts create the roles from these passwords |
| Images | `values-digests.yaml` | Install with the generated overlay; never `latest` |
| Ingress TLS | `ingress.tls.secretName` | Public certificate Secret ([ADR-0004](../../../ADR/ADR-0004-api-authentication-sessions-and-transport.md)) |
| DB TLS | `tls.db.*` | Secret `tls.db.secretName` with `tls.crt`/`tls.key`/`ca.crt`, or `tls.db.certManager.enabled=true`; without it the DB and its clients stay pending ([ADR-0017](../../../ADR/ADR-0017-database-tls-and-authentication.md)) |
| DB init | `infra.timescaledb.inCluster` | In-cluster: scripts run on a fresh data volume. External: run [the init scripts](../../../infrastructure/timescaledb/init-scripts/README.md) yourself before first use |
| Spark checkpoints | `apps.spark-*.persistence` | Default is a ReadWriteOnce PVC; needs a default StorageClass or `storageClass` |
| Object-store principals | `secrets.data.*_ACCESS_KEY` / `*_SECRET_KEY` | One pair per consumer, access keys distinct from the root's. External store: create equivalent principals; the policy table in `infrastructure/minio/minio-init.sh` is the reference |
| Device mTLS | `ingress.mtls.*` | Device-CA Secret `caSecret` with `ca.crt` and `ca.crl` ([device mTLS](../../../docs/operations/device-mtls.md)) |

### Object-store principals

Only the MinIO server and the `minio-init` Job hold `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`.

| Principal keys | Consumer |
|---|---|
| `MLFLOW_STORE_*` | MLflow server, read-only on the artifact bucket |
| `TRACKING_STORE_*` | Notebook tracking gateway |
| `MODEL_READ_*` | ml-service-api, read-only on admitted uploads ([ADR-0030](../../../ADR/ADR-0030-externally-authored-model-admission.md)) |
| `BACKUP_STORE_*` | db-backup CronJob |
| `SPARK_CHECKPOINT_*` | Spark jobs with an S3A checkpoint |
| `COLD_STORE_*` | cold-export CronJob (write) |
| `COLD_READ_*` | Notebook cold-store broker (read) |

### Spark checkpoint on the object store

Moving the Structured-Streaming checkpoint off the PVC ([ADR-0006](../../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md)), for both `spark-streaming` and `spark-aggregations`:

| Key | Value |
|---|---|
| `config.CHECKPOINT_LOCATION` | `s3a://spark-checkpoints` |
| `apps.<name>.secretKeys` | add `SPARK_CHECKPOINT_ACCESS_KEY`, `SPARK_CHECKPOINT_SECRET_KEY` |
| `apps.<name>.secretEnv` | `{ MINIO_ACCESS_KEY: SPARK_CHECKPOINT_ACCESS_KEY, MINIO_SECRET_KEY: SPARK_CHECKPOINT_SECRET_KEY }` |
| `apps.<name>.persistence.enabled` | `false` |

## Deferred

Open per ADR-0009 *Deferred decisions*: stateful operators, autoscaling policy, secrets backend,
Spark on Kubernetes. The notebook stack (`notebookHub`) renders but is not cluster-validated.

## Related

| Doc | Topic |
|---|---|
| [backup-and-recovery.md](../../../docs/operations/backup-and-recovery.md) | `backup.*`, restore |
| [monitoring.md](../../../docs/operations/monitoring.md) | `observability.*`, `deploy/observability` |
| [high-availability.md](../../../docs/operations/high-availability.md) | `resilience.*`, HPA |
| [notebooks.md](../../../docs/architecture/notebooks.md) | `notebookHub.*` |
| [ADR-0025](../../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) | `coldExport.*` |
