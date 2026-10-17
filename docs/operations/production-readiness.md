<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Production readiness

Production-readiness state per area and the open gaps. Item numbers are stable; ADRs cite them.

| # | Area | In place | Open gap |
|---|------|----------|----------|
| 1 | Release | `images.yml` builds every first-party image, smoke-starts it, gates on fixable CRITICAL (Trivy, empty `.trivyignore`), runs pip-audit / npm audit, pushes `:latest` and `:sha-<short>` to GHCR on `main`. Helm pins third-party images by digest in `values.yaml`; first-party digests via `scripts/pin-image-digests.sh latest > deploy/helm/industryflow/values-digests.yaml` ([ADR-0009](../../ADR/ADR-0009-kubernetes-deployment-and-packaging.md)). Release model: [ADR-0026](../../ADR/ADR-0026-release-model-and-the-compose-smoke-gate.md) | The PR path filter is workflow-wide: one changed build context rebuilds the whole matrix |
| 2 | Helm | TimescaleDB init scripts mounted from a ConfigMap (one source with Compose via `files/` symlinks); Spark checkpoint PVCs; observability integration (item 7); `helm lint` / `helm template` in CI | The chart has not been run on a live cluster |
| 3 | Data / DR | Scheduled `pg_dump` of every database + globals, cold-bucket mirror, TimescaleDB-aware restore ([backup-and-recovery.md](backup-and-recovery.md)) | Off-site target is opt-in; no WAL archiving / point-in-time recovery; model artifacts and notebook volumes not backed up ([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md), Proposed) |
| 4 | Secrets and database TLS | Per-workload Secrets, per-service least-privilege database roles, fail-closed placeholder secrets, `pg_hba` limited to private ranges, Compose host ports of internal services bound to `127.0.0.1`; database TLS `verify-full` + `hostssl` / `scram-sha-256` in Compose and Helm ([ADR-0017](../../ADR/ADR-0017-database-tls-and-authentication.md), [tls.md](tls.md)) | — |
| 5 | Containers | First-party images non-root with a restricted `securityContext` (`runAsNonRoot`, all capabilities dropped, no privilege escalation, seccomp `RuntimeDefault`, read-only root FS where applicable): uid 10001, Spark uid 185, frontend nginx uid 101 on 8080/8443. Notebook servers non-root on both spawners ([ADR-0018](../../ADR/ADR-0018-notebook-hub-spawner-portability.md)) | — |
| 6 | CI | Required gates per workflow ([ci.md](ci.md)); ruff, eslint, vitest; Dependabot for pip, npm, docker, docker-compose, GitHub Actions | `services/spark_jobs/tests/test_spark_service.py` is a live-stack harness, excluded from CI |
| 7 | Observability | Helm ServiceMonitors (apps with `metrics: true`), PrometheusRules, Grafana dashboard ConfigMaps, postgres / redis / kafka exporters (postgres-exporter as `metrics_user`); `ingestion-service` `/metrics` (denied at the mTLS edge); Spark PrometheusServlet ([ADR-0016](../../ADR/ADR-0016-observability-and-monitoring-integration.md), [monitoring.md](monitoring.md)) | — |
| 8 | Resilience | PodDisruptionBudgets, pod anti-affinity, opt-in CPU HPA ([high-availability.md](high-availability.md)) | Stateful backends are single instances; operators or managed HA are a per-environment choice (ADR-0009) |
| 9 | Spark / edge | `SPARK_MASTER` (default `local[*]`) set only in the job's SparkSession builder; the ingestion edge re-resolves `ingestion-service` per request (`resolver 127.0.0.11`, variable upstream) | — |
