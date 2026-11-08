<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Helm chart

The manual of [ADR-0009](../../../ADR/ADR-0009-kubernetes-deployment-packaged-with-helm.md): the chart's templates, values, workloads, datastore toggles, secrets, TLS edge, image pinning and pre-production steps.

## ADR-0009 dec 1

One chart, `industryflow`, packages the platform for Kubernetes. The notebook stack (`notebookHub`) renders but is not run on a cluster.

| Code | Role |
|---|---|
| `deploy/helm/industryflow/Chart.yaml` | The chart |
| `deploy/helm/industryflow/values.yaml` | Defaults and per-dependency toggles |
| `deploy/helm/industryflow/values-digests.yaml` | Generated first-party digest overlay |
| `scripts/pin-image-digests.sh` | Generates the digest overlay |
| `tests/config_guards/test_image_pins.py::test_every_chart_third_party_image_is_pinned_by_digest` | Every third-party image pinned by digest |

### Templates

| Template | Renders | Gate |
|---|---|---|
| `apps.yaml` | Deployment (+ Service, PDB, HPA) per entry in `apps` | `apps.<name>.enabled` |
| `configmap.yaml` | Shared non-secret config from `config` | always |
| `secret.yaml` | One scoped Secret per workload, `<release>-<workload>-secrets` | `secrets.create`, no `existingSecret` |
| `infra-timescaledb.yaml` | TimescaleDB StatefulSet + Service | `infra.timescaledb.inCluster` |
| `configmap-db-migrations.yaml` | `files/db-migrations` scripts and `files/migrate.sh`, for the runner and the `schema-ready` init containers | always |
| `job-db-migrate.yaml` | `<release>-db-migrate-<revision>` Job applying the schema sequence ([ADR-0034](../../../ADR/ADR-0034-platform-schema-migrations.md)) | always |
| `configmap-pg-hba.yaml` | Hardened `pg_hba.conf` + `postgresql.conf` (`files/db-config`) | `infra.timescaledb.inCluster` |
| `db-tls-certificate.yaml` | cert-manager issuers, internal CA, DB server cert | `tls.db.certManager.enabled` |
| `infra-kafka.yaml` | ZooKeeper + Kafka StatefulSets | `infra.zookeeper` / `infra.kafka.inCluster` |
| `infra-redis.yaml` | Redis StatefulSet | `infra.redis.inCluster` |
| `infra-minio.yaml` | MinIO StatefulSet | `infra.minio.inCluster` |
| `infra-mlflow.yaml` | MLflow Deployment | `infra.mlflow.inCluster` |
| `job-minio-init.yaml` | post-install/upgrade Job running `files/minio-init.sh` | `infra.minio.inCluster` |
| `cronjob-db-backup.yaml` | Logical DB backup and restore-rehearsal CronJobs; `<release>-pre-upgrade-backup` hook Job ([ADR-0032](../../../ADR/ADR-0032-backup-and-disaster-recovery-for-every-durable-store.md) dec 7) | `backup.enabled` |
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
| `cronjob-tenant-delete.yaml` | Tenant-delete CronJob ([ADR-0033](../../../ADR/ADR-0033-tenant-lifecycle.md) dec 4) | `tenantDelete.enabled` |
| `cronjob-tracking-collect.yaml` | Tracking-store collection CronJob for deleted tenants | `tenantDelete.enabled`, `notebookHub.enabled`, `infra.mlflow.inCluster` |
| `ops-agent.yaml` | Infrastructure agent Deployment, Service and NetworkPolicy | `observability.opsAgent.enabled` |

### Commands

| Task | Command |
|---|---|
| Render locally | `helm template if deploy/helm/industryflow --set secrets.failOnPlaceholders=false` |
| Lint | `helm lint deploy/helm/industryflow --set secrets.failOnPlaceholders=false` |
| Generate the digest overlay | `scripts/pin-image-digests.sh <tag> > deploy/helm/industryflow/values-digests.yaml` |
| Install | `helm install industryflow deploy/helm/industryflow --namespace industryflow --create-namespace -f deploy/helm/industryflow/values-digests.yaml -f <secrets-values>` |

### Image digest pinning

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

### Before production

| Item | Keys | Requirement |
|---|---|---|
| Secrets | `secrets.data`, `secrets.create`, `secrets.existingSecret` | Replace every `CHANGE_ME` from a secrets backend, or `secrets.create=false` + `existingSecret` (every workload then reads only its own keys from that one Secret). Rendering fails on a placeholder while `secrets.failOnPlaceholders=true` |
| DB roles | `secrets.data.*_DB_PASSWORD`, `config.*_DB_USER` | Each service connects as its own role; the `db-migrate` Job creates the roles from these passwords |
| Images | `values-digests.yaml` | Install with the generated overlay; never `latest` |
| Ingress TLS | `ingress.tls.secretName` | Public certificate Secret ([ADR-0004](../../../ADR/ADR-0004-api-authentication-session-model-and-transport-security.md)) |
| DB TLS | `tls.db.*` | Secret or cert-manager ([database TLS](../../../docs/operations/tls.md#helm)) |
| DB schema | `dbMigrations.*` | The `db-migrate` Job applies [the schema sequence](../../../infrastructure/timescaledb/init-scripts/README.md) to the in-cluster or external database on every install and upgrade; each database client's `schema-ready` init container waits up to `dbMigrations.waitSeconds` for a current ledger. The `<release>-pre-upgrade-backup` hook backs up first; the Job refuses pending scripts without a backup newer than `dbMigrations.backupMaxAgeSeconds` unless `dbMigrations.withoutBackup` ([before an upgrade](../../../docs/operations/backup-and-recovery.md#before-an-upgrade)) |
| Spark checkpoints | `apps.spark-*.persistence` | Default is a ReadWriteOnce PVC; needs a default StorageClass or `storageClass` |
| Object-store principals | `secrets.data.*_ACCESS_KEY` / `*_SECRET_KEY` | One pair per consumer, access keys distinct from the root's. External store: create equivalent principals; the policy table in `infrastructure/minio/minio-init.sh` is the reference (ADR-0025 dec 10, ADR-0030 dec 2, ADR-0038 dec 3) |
| Device mTLS | `ingress.mtls.*` | Device CA Secret with `ca.crt` and `ca.crl` ([device mTLS](../../../docs/operations/device-mtls.md#kubernetes)) |

## ADR-0009 dec 2

| App setting | Renders |
|---|---|
| each `apps.<name>` | A Deployment (+ Service) |
| `apps.<name>.probe` (`path`, `port`) | HTTP liveness and readiness probes |
| `apps.<name>.livenessExec` | An exec liveness probe, for workers without a port |
| `apps.<name>.resources` | The container's resource requests and limits |
| `apps.<name>.persistence.enabled` | A ReadWriteOnce checkpoint volume and `strategy: Recreate`: the old pod stops before the new one starts |

The tracking server (`infra-mlflow.yaml`) is a Deployment with a readiness probe and resources. Disruption budgets and autoscaling: [high availability](../../../docs/operations/high-availability.md#adr-0009-dec-2).

| Code | Role |
|---|---|
| `deploy/helm/industryflow/templates/apps.yaml` | Deployments, probes, resources, `Recreate` for a checkpoint volume |
| `deploy/helm/industryflow/templates/infra-mlflow.yaml` | Tracking server Deployment |

### Spark checkpoint on the object store

Moving the Structured-Streaming checkpoint off the PVC ([ADR-0006](../../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md)), for both `spark-streaming` and `spark-aggregations`:

| Key | Value |
|---|---|
| `config.CHECKPOINT_LOCATION` | `s3a://spark-checkpoints` |
| `apps.<name>.secretKeys` | add `SPARK_CHECKPOINT_ACCESS_KEY`, `SPARK_CHECKPOINT_SECRET_KEY` |
| `apps.<name>.secretEnv` | `{ MINIO_ACCESS_KEY: SPARK_CHECKPOINT_ACCESS_KEY, MINIO_SECRET_KEY: SPARK_CHECKPOINT_SECRET_KEY }` |
| `apps.<name>.persistence.enabled` | `false` |

## ADR-0009 dec 3

| Dependency | `inCluster: true` renders |
|---|---|
| `timescaledb` | StatefulSet + Service |
| `kafka` | ZooKeeper and Kafka StatefulSets + Services |
| `redis` | StatefulSet + Service |
| `minio` | StatefulSet + Service |
| `mlflow` | Deployment + Service (stateless, ADR-0009 dec 2) |

With `inCluster: false` the endpoint helper returns `infra.<dep>.external.*` and rendering fails when it is empty.

| Code | Role |
|---|---|
| `deploy/helm/industryflow/values.yaml` | `infra.<dep>.inCluster` toggles and `external.*` endpoints |
| `deploy/helm/industryflow/templates/infra-timescaledb.yaml` | Database StatefulSet |
| `deploy/helm/industryflow/templates/infra-kafka.yaml` | Coordinator and broker StatefulSets |
| `deploy/helm/industryflow/templates/infra-redis.yaml` | Key-value store StatefulSet |
| `deploy/helm/industryflow/templates/infra-minio.yaml` | Object store StatefulSet |
| `deploy/helm/industryflow/templates/_helpers.tpl` | `industryflow.dbHost`, `kafkaBootstrap`, `redisHost`, `minioEndpoint`, `mlflowUri`: in-cluster or required external endpoint |

### In-cluster vs external datastores

Each stateful dependency under `infra` has an `inCluster` toggle.

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

## ADR-0009 dec 4

| Element | Mechanism |
|---|---|
| Configuration | `config` renders one ConfigMap; every app takes it by `envFrom` |
| Secret values | `secrets.data`, supplied at install from outside the chart |
| Placeholder | A value starting `CHANGE_ME` or `changeme` fails the render while `secrets.failOnPlaceholders=true` |
| Scoping | One Secret per workload, `<release>-<workload>-secrets`, holding only `apps.<name>.secretKeys` or the infra key list |
| Key delivery | Each key a workload uses is one `secretKeyRef` by name; no workload takes a Secret by `envFrom` |
| `secrets.existingSecret` | Every workload references that one Secret, by `secretKeyRef` to its own keys only; the chart renders no Secret and so no placeholder check |

| Code | Role |
|---|---|
| `deploy/helm/industryflow/templates/configmap.yaml` | The shared ConfigMap |
| `deploy/helm/industryflow/templates/secret.yaml` | Placeholder refusal; scoped Secrets per workload |
| `deploy/helm/industryflow/templates/_helpers.tpl` | `industryflow.scopedSecret`, `industryflow.workloadSecretName` |
| `deploy/helm/industryflow/templates/apps.yaml` | Mounts the ConfigMap; one `secretKeyRef` per `apps.<name>.secretKeys` entry |
| `deploy/helm/industryflow/templates/infra-mlflow.yaml` | Tracking server: one `secretKeyRef` per key it uses |
| `tests/config_guards/test_helm_secret_scoping.py` | No `envFrom` Secret; the same keys per workload with and without `existingSecret`; placeholder refusal |
| `deploy/helm/industryflow/values.yaml` | `secrets.*` and per-app `secretKeys` |

### Object-store principals

Only the MinIO server and the `minio-init` Job hold `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY`.

| Principal keys | Consumer |
|---|---|
| `MLFLOW_STORE_*` | MLflow server, read-only on the artifact bucket |
| `TRACKING_STORE_*` | Notebook tracking gateway |
| `MODEL_READ_*` | ml-service-api, read-only on admitted uploads ([ADR-0030](../../../ADR/ADR-0030-admission-of-externally-authored-models.md)) |
| `BACKUP_STORE_*` | db-backup CronJob (writes the backup bucket) |
| `BACKUP_READ_*` | db-backup CronJob, read-only on the artifact and cold buckets it mirrors ([ADR-0038](../../../ADR/ADR-0038-model-artifacts-are-mirrored-notebook-volumes-archived-by-the-backup-job.md)) |
| `SPARK_CHECKPOINT_*` | Spark jobs with an S3A checkpoint |
| `COLD_STORE_*` | cold-export CronJob (write) |
| `COLD_READ_*` | Notebook cold-store broker (read) |

## ADR-0009 dec 5

| Ingress | Host | Controller does |
|---|---|---|
| `<release>` | Each enabled app's `ingress.host` | Terminates HTTPS with `ingress.tls.secretName` |
| `<release>-ingest` | `ingress.mtls.host` | Verifies device client certificates against `ingress.mtls.caSecret`; passes the verified certificate to ingestion |

| Element | Value |
|---|---|
| `networkPolicy.enabled` | `true` by default; a CNI without NetworkPolicy support ignores the policies |
| `<release>-ingestion-from-edge-only` | Admits ingress to ingestion only from pods matching `networkPolicy.ingressControllerPodLabels` (default `app.kubernetes.io/name: ingress-nginx`, `app.kubernetes.io/component: controller`) in the namespace `networkPolicy.ingressControllerNamespace`; empty labels fail the render |
| `networkPolicy.externalEgress.<dep>` | `[{cidr, port}]` for an external `timescaledb`, `redis`, `kafka`, `minio` or `mlflow`; a restricted workload using an external dependency with no entry fails the render |
| `ingress.mtls.enabled` | `false` by default: no Ingress routes to ingestion, so it is unreachable rather than reachable without mTLS; enabling it needs the device-CA Secret `ingress.mtls.caSecret` |
| Ingestion metrics | A `metrics-listener` container (`apps.ingestion-service.metricsListener`, nginx) serves only `/metrics` from the app on loopback, 404 for every other path; the Service and ServiceMonitor name it as port `metrics` |
| Ingestion's admitted callers | The controller pods to the app port only; `observability.prometheusPodLabels` pods in `observability.monitoringNamespace` to the metrics port only; empty labels fail the render |

| Code | Role |
|---|---|
| `deploy/helm/industryflow/templates/ingress.yaml` | HTTPS Ingress and device mTLS Ingress |
| `deploy/helm/industryflow/templates/networkpolicy.yaml` | Ingestion reachable only from the controller pods; per-workload egress |
| `deploy/helm/industryflow/templates/_helpers.tpl` | `industryflow.egressTo`: in-cluster pod selector or `externalEgress` ipBlocks |
| `deploy/helm/industryflow/values.yaml` | `ingress.*`, `networkPolicy.*` |
| `deploy/helm/industryflow/templates/apps.yaml` | Metrics-only listener container and its ConfigMap |
| `deploy/helm/industryflow/templates/observability-servicemonitors.yaml` | Scrapes the listener's port |
| `tests/config_guards/test_helm_ingestion_edge_policy.py` | Policy on by default; controller pods to the app port, Prometheus to the metrics port, nothing else; the listener serves only `/metrics` and is the port scraped; only the mTLS Ingress routes to ingestion; external egress required |

## ADR-0009 dec 6

The chart reads the Compose deployment's own sources through symlinks; neither copies them. The two deployments pin each third-party image identically.

### Compose sources

`files/` holds symlinks to the compose sources; Helm follows them when it loads the chart.

| Symlink | Target |
|---|---|
| `files/db-migrations` | `infrastructure/timescaledb/init-scripts` |
| `files/migrate.sh` | `infrastructure/timescaledb/migrate.sh` |
| `files/db-config` | `infrastructure/timescaledb/config` |
| `files/minio-init.sh` | `infrastructure/minio/minio-init.sh` |
| `files/device-ca.sh` | `scripts/device-ca.sh` |
| `files/grafana` | `infrastructure/grafana/provisioning` |
| `files/prometheus-rules.yml` | `infrastructure/prometheus/rules/platform.yml` |

| Code | Role |
|---|---|
| `deploy/helm/industryflow/files/db-migrations` | Schema scripts from the Compose source |
| `infrastructure/timescaledb/migrate.sh` | Migration runner, read by the chart through `files/migrate.sh` |
| `deploy/helm/industryflow/templates/observability-prometheusrules.yaml` | Renders the platform alert rules from `files/prometheus-rules.yml` |
| `tests/config_guards/test_image_pins.py::test_compose_and_chart_pin_the_same_image_identically` | Same third-party pin in both deployments |

Links: [backup and recovery](../../../docs/operations/backup-and-recovery.md#kubernetes-helm) (`backup.*`, restore); [cluster monitoring](../../observability/README.md) (`observability.*`); [high availability](../../../docs/operations/high-availability.md) (`resilience.*`, HPA); [notebooks](../../../docs/architecture/notebooks.md#deployment-profiles) (`notebookHub.*`); [cold layer](../../../docs/architecture/data-and-storage.md#cold-layer) (`coldExport.*`).
