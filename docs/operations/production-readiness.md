<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Production readiness

Readiness per area and the open gaps. Item numbers are stable; ADRs cite them.

| # | Area | In place | Open gap |
|---|---|---|---|
| 1 | Release | `images.yml` builds, smoke-starts and scans every first-party image, pushes `:latest` and `:sha-<short>` to GHCR on `main` ([CI](ci.md)); Helm pins images by digest ([Helm chart](../../deploy/helm/industryflow/README.md#image-digest-pinning)); release model [ADR-0026](../../ADR/ADR-0026-release-model-and-the-compose-smoke-gate.md) | The PR path filter is workflow-wide: one changed build context rebuilds the whole matrix |
| 2 | Helm | Init scripts from a ConfigMap sharing the Compose sources; Spark checkpoint PVCs; observability integration (item 7); `helm lint` / `helm template` in CI | The chart has not been run on a live cluster |
| 3 | Data / DR | Scheduled dumps, cold-bucket mirror, TimescaleDB-aware restore ([backup and recovery](backup-and-recovery.md)) | Off-site target is opt-in; no WAL archiving / point-in-time recovery; model artifacts and notebook volumes not backed up ([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md)) |
| 4 | Secrets and database TLS | Per-workload Secrets, per-service database roles, fail-closed placeholder secrets, `pg_hba` limited to private ranges, internal Compose ports on `127.0.0.1`; database TLS `verify-full` ([TLS](tls.md#database-tls), [ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md)) | — |
| 5 | Containers | First-party images non-root with a restricted `securityContext` (`runAsNonRoot`, all capabilities dropped, no privilege escalation, seccomp `RuntimeDefault`, read-only root FS where applicable): uid 10001, Spark uid 185, frontend nginx uid 101. Notebook servers non-root on both spawners ([ADR-0018](../../ADR/ADR-0018-notebook-hub-spawner-portability.md)) | — |
| 6 | CI | Required gates ([CI](ci.md)); Dependabot for pip, npm, docker, docker-compose, GitHub Actions | `services/spark_jobs/tests/test_spark_service.py` is a live-stack harness, excluded from CI |
| 7 | Observability | ServiceMonitors, PrometheusRules, dashboard ConfigMaps, data-store exporters ([cluster monitoring](../../deploy/observability/README.md)); Compose stack ([monitoring](monitoring.md)) | — |
| 8 | Resilience | PodDisruptionBudgets, pod anti-affinity, opt-in CPU HPA ([high availability](high-availability.md)) | Stateful backends are single instances; operators or managed HA are a per-environment choice ([ADR-0009](../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md)) |
| 9 | Spark / edge | `SPARK_MASTER` (default `local[*]`) set only in the job's SparkSession builder; the ingestion edge re-resolves `ingestion-service` per request (`resolver 127.0.0.11`, variable upstream) | — |
| 10 | Capacity | One Compose node with 8 GB and 4 cores, running the whole platform, sustains 500 readings/s ([capacity](capacity.md)) | Ingest is one reading per HTTPS request; past ~875 readings/s a single host is CPU-bound |
