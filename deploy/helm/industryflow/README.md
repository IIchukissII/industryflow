<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Helm chart

Kubernetes packaging for IndustryFlow ([ADR-0009](../../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md)): templates, values, image pinning and pre-production steps.

## Templates

| Template | Renders | Gate |
|---|---|---|
| `apps.yaml` | Deployment (+ Service, PDB, HPA) per entry in `apps` | `apps.<name>.enabled` |
| `configmap.yaml` | Shared non-secret config from `config` | always |
| `secret.yaml` | One scoped Secret per workload, `<release>-<workload>-secrets` | `secrets.create`, no `existingSecret` |
| `infra-timescaledb.yaml` | TimescaleDB StatefulSet + Service | `infra.timescaledb.inCluster` |
| `configmap-db-migrations.yaml` | `files/db-migrations` scripts and `files/migrate.sh`, for the runner and the `schema-ready` init containers | always |
| `job-db-migrate.yaml` | `<release>-db-migrate-<revision>` Job applying the schema sequence ([ADR-0034](../../../ADR/ADR-0034-platform-schema-migration-strategy.md)) | always |
| `configmap-pg-hba.yaml` | Hardened `pg_hba.conf` + `postgresql.conf` (`files/db-config`) | `infra.timescaledb.inCluster` |
| `db-tls-certificate.yaml` | cert-manager issuers, internal CA, DB server cert | `tls.db.certManager.enabled` |
| `infra-kafka.yaml` | ZooKeeper + Kafka StatefulSets | `infra.zookeeper` / `infra.kafka.inCluster` |
| `infra-redis.yaml` | Redis StatefulSet | `infra.redis.inCluster` |
| `infra-minio.yaml` | MinIO StatefulSet | `infra.minio.inCluster` |
| `infra-mlflow.yaml` | MLflow Deployment | `infra.mlflow.inCluster` |
| `job-minio-init.yaml` | post-install/upgrade Job running `files/minio-init.sh` | `infra.minio.inCluster` |
| `cronjob-db-backup.yaml` | Logical DB backup CronJob | `backup.enabled` |
| `cronjob-cold-export.yaml` | Cold-layer export CronJob | `coldExport.enabled` |
| `cronjob-device-crl-check.yaml` | Device CRL and CA certificate expiry check CronJob | `ingress.enabled`, `ingress.mtls.enabled`, `ingress.mtls.crlCheck.enabled` |
| `cronjob-device-cert-check.yaml` | Device certificate expiry check CronJob | `ingress.enabled`, `ingress.mtls.enabled`, `ingress.mtls.certCheck.enabled` |
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
| `files/db-migrations` | `infrastructure/timescaledb/init-scripts` |
| `files/migrate.sh` | `infrastructure/timescaledb/migrate.sh` |
| `files/db-config` | `infrastructure/timescaledb/config` |
| `files/minio-init.sh` | `infrastructure/minio/minio-init.sh` |
| `files/device-ca.sh` | `scripts/device-ca.sh` |
| `files/grafana` | `infrastructure/grafana/provisioning` |

## In-cluster vs external datastores

Each stateful dependency under `infra` has an `inCluster` toggle ([ADR-0009](../../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md) dec 3).

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

| Name | Default | Meaning |
|---|---|---|
| `REGISTRY` | `image.registry` | Registry/namespace prefix |
| `GITHUB_TOKEN` | `gh auth token` | Token for private packages |

## Before production

| Item | Keys | Requirement |
|---|---|---|
| Secrets | `secrets.data`, `secrets.create`, `secrets.existingSecret` | Replace every `CHANGE_ME` from a secrets backend, or `secrets.create=false` + `existingSecret` (every workload then references that one Secret). Rendering fails on a placeholder while `secrets.failOnPlaceholders=true` |
| DB roles | `secrets.data.*_DB_PASSWORD`, `config.*_DB_USER` | Each service connects as its own role; the `db-migrate` Job creates the roles from these passwords |
| Images | `values-digests.yaml` | Install with the generated overlay; never `latest` |
| Ingress TLS | `ingress.tls.secretName` | Public certificate Secret ([ADR-0004](../../../ADR/ADR-0004-api-authentication-sessions-and-transport.md)) |
| DB TLS | `tls.db.*` | Secret or cert-manager ([database TLS](../../../docs/operations/tls.md#helm)) |
| DB schema | `dbMigrations.*` | The `db-migrate` Job applies [the schema sequence](../../../infrastructure/timescaledb/init-scripts/README.md) to the in-cluster or external database on every install and upgrade; each database client's `schema-ready` init container waits up to `dbMigrations.waitSeconds` for a current ledger |
| Spark checkpoints | `apps.spark-*.persistence` | Default is a ReadWriteOnce PVC; needs a default StorageClass or `storageClass` |
| Object-store principals | `secrets.data.*_ACCESS_KEY` / `*_SECRET_KEY` | One pair per consumer, access keys distinct from the root's. External store: create equivalent principals; the policy table in `infrastructure/minio/minio-init.sh` is the reference |
| Device mTLS | `ingress.mtls.*` | Device CA Secret with `ca.crt` and `ca.crl` ([device mTLS](../../../docs/operations/device-mtls.md#kubernetes)) |

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

Open per [ADR-0009](../../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md) *Deferred decisions*: stateful operators, autoscaling policy, secrets backend,
Spark on Kubernetes. The notebook stack (`notebookHub`) renders but is not run on a cluster.

## Related

| Page | Topic |
|---|---|
| [backup and recovery](../../../docs/operations/backup-and-recovery.md#kubernetes-helm) | `backup.*`, restore |
| [cluster monitoring](../../observability/README.md) | `observability.*` |
| [high availability](../../../docs/operations/high-availability.md) | `resilience.*`, HPA |
| [notebooks](../../../docs/architecture/notebooks.md#deployment-profiles) | `notebookHub.*` |
| [cold layer](../../../docs/architecture/data-and-storage.md#cold-layer) | `coldExport.*` |
