<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Production-readiness review (snapshot: 2026-06-27)

A point-in-time DevOps/production-readiness assessment and the resulting backlog. This is a
**snapshot**, not a living spec — re-run the review and supersede it rather than editing in
place. The platform's *application-layer* security (mTLS ingestion, per-tenant DB roles +
proven isolation, CSRF, Kafka DLQ/idempotence) is mature; the gaps are concentrated in release
engineering, the Helm path, backups, and HA/scaling.

## Prioritized backlog

| # | Area | Severity | Gap | Action |
|---|------|----------|-----|--------|
| 1 | Release | HIGH → **mostly done** | `images.yml` builds all 13 images, Trivy-**gates on fixable CRITICAL** (`exit-code 1`), pip-audit/npm-audit, pushes `:latest`+`:sha-<short>` to GHCR on main. CRITICALs remediated: `mlflow` 2.8/2.9→3.14.0, `python-jose` 3.3→3.4, `pyarrow` (transitive ≥14); `evidently` 0.4.11→0.4.40 to lift its `pydantic<2` cap (build conflict with mlflow 3.x). Spark-distro JARs (avro/derby/zookeeper) accepted in `.trivyignore` (justified, expiry) pending a Spark upgrade. Helm now pins images by **immutable digest** (third-party in `values.yaml`; first-party via `scripts/pin-image-digests.sh` → `values-digests.yaml`; ADR-0009). **Remaining:** (a) upgrade Spark to drop the `.trivyignore` entries; (b) per-image path filters in `images.yml` | Spark upgrade |
| 2 | Helm | HIGH → **mostly done** | Chart is now cluster-functional: TimescaleDB init scripts mounted via ConfigMap at `/docker-entrypoint-initdb.d` (roles/schemas/tenant machinery created on a fresh volume; single source of truth via a `files/db-init` symlink), and `spark-streaming`/`spark-aggregations` get ReadWriteOnce checkpoint PVCs (ADR-0009). **Remaining:** observability stack (Prometheus/Grafana/Loki/exporters) not yet ported to Helm — tracked as item 7 and an ADR-0009 deferred decision | Port the observability stack (item 7) |
| 3 | Data/DR | HIGH → **tier-1 done** | Scheduled logical backups (`pg_dump -Fc` of every database + globals) to S3/MinIO with retention, as a Helm CronJob / compose `backup` profile, plus a TimescaleDB-aware restore tool and runbook (`docs/operations/backup-and-recovery.md`). RPO = schedule interval. **Remaining:** (a) target an **off-site** bucket for true DR (default is in-cluster MinIO); (b) tier-2 continuous-WAL **PITR** (pgBackRest) for seconds-level RPO | pgBackRest PITR; off-site repo |
| 4 | Secrets | HIGH → **mostly done** | Done: Helm renders a **scoped Secret per workload** (only the keys it needs — no pod gets the full bundle), each service connects as its **own least-privilege DB role** (not the superuser), `helm install` **fails closed** on un-overridden `CHANGE_ME` placeholders (`secrets.failOnPlaceholders`), `pg_hba` is **scoped to private (RFC1918) networks** — no superuser/replication from `0.0.0.0/0`, mounted in Helm too — and compose **binds DB/Kafka/Redis/ZK host ports to localhost**. **Remaining:** DB **TLS** (`hostssl`) + `scram-sha-256` (needs a server cert + roles with SCRAM verifiers) | Provision a DB server cert, switch `pg_hba` to `hostssl`+scram |
| 5 | Containers | HIGH → **mostly done** | All first-party app/infra images run **non-root** (`USER 10001`; Spark already uid 185), and the chart applies a restricted `securityContext` to every app + mlflow + db-backup: `runAsNonRoot`, drop **all** caps, `allowPrivilegeEscalation=false`, seccomp `RuntimeDefault`, and read-only root FS with writable scratch `emptyDir`s. **Remaining:** the **frontend** (nginx) still runs as root on :80/:443 (opted out via `hardened: false`) and the spawned **jupyter** image uses `--allow-root` | Switch frontend to an unprivileged nginx (listen 8080); set KubeSpawner `runAsUser` for jupyter |
| 6 | CI | MED → **done** | pytest matrix now also runs `ml_service` (feature store), `spark_jobs` (FK-resilience filter) and `alert_service` (new threshold rules-engine test); **ruff** (Python, blocking) and **eslint** (frontend, blocking) lint gates added; pip-audit/npm-audit already run; **Dependabot** covers pip/npm/docker/actions. **Note:** the frontend has no unit tests yet (covered by eslint + the build); the Spark `test_spark_service.py` live-DB harness stays manual | Add frontend component tests later |
| 7 | Observability | MED → **mostly done** | Helm now emits the monitoring integration (ADR-0016): data-driven **ServiceMonitors** (any app with `metrics: true`), platform **PrometheusRules** (target-down, pg/redis down, Kafka lag), **Grafana-dashboard** ConfigMaps, and the postgres/redis/kafka **exporters** (postgres-exporter on a least-priv `metrics_user`/`pg_monitor` role). Backend (`kube-prometheus-stack` + Loki) runs separately (`deploy/observability/`). Tenant sensor alerts stay tenant-isolated, off the shared stack. **Remaining:** instrument `ingestion-service` + Spark to expose `/metrics` (no exporter today) | Add `/metrics` to ingestion + Spark |
| 8 | Resilience | MED | SPOFs (1× TimescaleDB/Kafka/ZK/Redis/MinIO/Spark worker); no PDB/anti-affinity/HPA | PDBs + anti-affinity; Kafka-lag HPA for stateless consumers; HA path for Kafka/TimescaleDB |
| 9 | Spark / edge | MED → **done** | Spark master now has one source of truth — `SPARK_MASTER` (default `local[*]`) in the job's SparkSession builder; the misleading `--master local[*]` removed from both Spark Dockerfile CMDs (it was a no-op the builder overrode). Ingestion edge nginx now uses `resolver 127.0.0.11` + a variable upstream, so it re-resolves `ingestion-service` per request instead of caching the boot-time IP (fixes the 502-after-recreate). | — |

ADR-consistency drift (tracked separately): see the per-ADR audit. Top items — reset/verification
token logging (ADR-0004, fixed), `KAFKA_GROUP_ID` compose↔helm fork (ADR-0000, fixed), the
`company_id→schema` helper still duplicated across 7 services (ADR-0003 dec 7, open).

## Strengths (keep)

- Per-service least-privilege DB roles + per-tenant read-only role, **proven in CI**
  (`db-tenant-isolation`).
- mTLS device ingestion with CA+CRL; ingestion holds no DB role and no host port.
- httpOnly cookies + double-submit CSRF + short JWT/rotating refresh; CORS allowlist.
- Kafka at-least-once with manual commit, DLQ, idempotent producer/consumer; Spark idempotent
  upserts with FK-resilient filtering.
