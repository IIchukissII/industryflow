<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Production-readiness review (snapshot: 2026-06-27, stale items reconciled 2026-10-13)

A point-in-time DevOps/production-readiness assessment and the resulting backlog. This is a
**snapshot**, not a living spec — re-run the review and supersede it rather than editing in
place. (The 2026-10-13 reconciliation only corrected statements the code had made false; it is
not a new review.) The platform's *application-layer* security (mTLS ingestion, per-tenant DB roles +
proven isolation, CSRF, Kafka DLQ/idempotence) is mature; the gaps are concentrated in release
engineering, the Helm path, backups, and HA/scaling.

## Prioritized backlog

| # | Area | Severity | Gap | Action |
|---|------|----------|-----|--------|
| 1 | Release | HIGH → **mostly done** | `images.yml` builds every first-party image (its build matrix is the list), Trivy-**gates on fixable CRITICAL** (`exit-code 1`), pip-audit/npm-audit, pushes `:latest`+`:sha-<short>` to GHCR on main. CRITICALs remediated: `mlflow` 2.8/2.9→3.15.0, `python-jose` 3.3→3.4, `pyarrow` (transitive ≥14); `evidently` 0.4.11→0.4.40 to lift its `pydantic<2` cap (build conflict with mlflow 3.x). Spark-distro JARs are patched at build (netty set replaced, Derby and the Connect REPL removed); `.trivyignore` accepts nothing. Helm now pins images by **immutable digest** (third-party in `values.yaml`; first-party via `scripts/pin-image-digests.sh` → `values-digests.yaml`; ADR-0009). A workflow-level paths filter skips the build when no image input changed. **Remaining:** per-image path filters (one changed Dockerfile still rebuilds the whole matrix) | — |
| 2 | Helm | HIGH → **mostly done** | Chart is now cluster-functional: TimescaleDB init scripts mounted via ConfigMap at `/docker-entrypoint-initdb.d` (roles/schemas/tenant machinery created on a fresh volume; single source of truth via a `files/db-init` symlink), and `spark-streaming`/`spark-aggregations` get ReadWriteOnce checkpoint PVCs in the default local mode (ADR-0009; ADR-0006 rev 1 moves the checkpoint to the object store when Spark runs multi-node). The observability integration is ported (item 7). **Remaining:** the chart has not run on a live cluster | Run the chart on a cluster |
| 3 | Data/DR | HIGH → **tier-1 done** | Scheduled logical backups (`pg_dump -Fc` of every database + globals) to S3/MinIO with retention, as a Helm CronJob / compose `backup` profile, plus a TimescaleDB-aware restore tool and runbook (`docs/operations/backup-and-recovery.md`). RPO = schedule interval. **Remaining:** (a) target an **off-site** bucket for true DR (default is in-cluster MinIO); (b) tier-2 continuous-WAL **PITR** (pgBackRest) for seconds-level RPO; (c) stores the database dump does not contain — the cold layer (the only copy of raw history past the hot horizon), model artifacts, notebook volumes. ADR-0032 (Proposed) records the decision these conform to | pgBackRest PITR; off-site repo; non-database stores (ADR-0032) |
| 4 | Secrets | HIGH → **done** | Per-workload scoped Secrets, least-privilege per-service DB roles, fail-closed placeholders, `pg_hba` private-net-scoped, compose host ports on localhost — and **DB TLS** (ADR-0017): clients connect `verify-full` (encrypted + CA-verified) and `pg_hba` enforces `hostssl` + `scram-sha-256` (md5 retired). Staged + live-validated on compose; the **Helm** chart mirrors it (`tls.db`: cert-manager self-provisioning or a bring-your-own Secret, a cert-init initContainer, the CA mounted into every DB client, a `metrics_user` `hostssl` rule). | — |
| 5 | Containers | HIGH → **done** | **All** first-party images run **non-root** with a restricted `securityContext` (`runAsNonRoot`, drop **all** caps, `allowPrivilegeEscalation=false`, seccomp `RuntimeDefault`, read-only root FS + writable scratch where applicable): app/infra/db-backup as uid 10001, Spark uid 185, and the **frontend** now an **unprivileged nginx** (uid 101, listens 8080/8443). The per-user notebook path is non-root by design — KubeSpawner already runs `runAsNonRoot`/uid 1000/drop-caps, and **ADR-0018** makes that binding on the compose (DockerSpawner) profile too. The legacy standalone root `jupyter` shim is **retired**: the dual-spawner hub replaced it (ADR-0018 dec 5), and no compose service or chart template runs `--allow-root`. | — |
| 6 | CI | MED → **done** | pytest matrix now also runs `ml_service`, `spark_jobs` (FK-resilience filter) and `alert_service` (new threshold rules-engine test); **ruff** (Python, blocking) and **eslint** (frontend, blocking) lint gates added; pip-audit/npm-audit already run; **Dependabot** covers pip/npm/docker/actions. The frontend now has **vitest** component/unit tests, run in CI beside eslint. **Note:** the Spark `test_spark_service.py` live-DB harness stays manual | — |
| 7 | Observability | MED → **done** | Helm emits the monitoring integration (ADR-0016): data-driven **ServiceMonitors** (any app with `metrics: true`), platform **PrometheusRules** (target-down, pg/redis down, Kafka lag), **Grafana-dashboard** ConfigMaps, and the postgres/redis/kafka **exporters** (postgres-exporter on a least-priv `metrics_user`/`pg_monitor` role). Backend (`kube-prometheus-stack` + Loki) runs separately (`deploy/observability/`). Tenant sensor alerts stay tenant-isolated, off the shared stack. **`ingestion-service`** now exposes `/metrics` (FastAPI instrumentator; denied at the mTLS edge, scraped in-cluster), and **Spark** exposes its native **PrometheusServlet** (driver/executor/master metrics on the existing UI ports) — both wired into compose Prometheus and Helm ServiceMonitors. | — |
| 8 | Resilience | MED → **mostly done** | Stateless tier now has **PodDisruptionBudgets** (multi-replica apps), **pod anti-affinity** (soft/hard), and opt-in CPU **HPA** (`apps.<name>.hpa`; Deployment yields `replicas` to it). HA path for the stateful SPOFs (TimescaleDB/Kafka/ZK/Redis/MinIO/Spark) and Kafka-lag autoscaling via KEDA documented in `docs/operations/high-availability.md`. **Remaining:** actually adopting the stateful operators/managed HA is a per-environment deployment choice (ADR-0009 deferred) | Adopt operators per environment |
| 9 | Spark / edge | MED → **done** | Spark master now has one source of truth — `SPARK_MASTER` (default `local[*]`) in the job's SparkSession builder; the misleading `--master local[*]` removed from both Spark Dockerfile CMDs (it was a no-op the builder overrode). Ingestion edge nginx now uses `resolver 127.0.0.11` + a variable upstream, so it re-resolves `ingestion-service` per request instead of caching the boot-time IP (fixes the 502-after-recreate). | — |

ADR-consistency drift (tracked separately): see the per-ADR audit. Top items — reset/verification
token logging (ADR-0004, fixed), `KAFKA_GROUP_ID` compose↔helm fork (ADR-0000, fixed), the
`company_id→schema` mapping re-implemented in several services (ADR-0003 dec 5/7, fixed: every service
now takes its tenant names from `libs/industryflow_contracts`, and a repository guard fails on an inline copy).

## Strengths (keep)

- Per-service least-privilege DB roles + per-tenant read-only role, **proven in CI**
  (`db-tenant-isolation`).
- mTLS device ingestion with CA+CRL; ingestion holds no DB role and no host port.
- httpOnly cookies + double-submit CSRF + short JWT/rotating refresh; CORS allowlist.
- Kafka at-least-once with manual commit, DLQ, idempotent producer/consumer; Spark idempotent
  upserts with FK-resilient filtering.
