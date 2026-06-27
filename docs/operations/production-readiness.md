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
| 1 | Release | HIGH → **in progress** | ~~No image build/publish/scan pipeline~~ — `images.yml` now builds all 13 images, Trivy-scans (fail on fixable CRITICAL), pip-audit/npm-audit (report), and pushes `:latest`+`:sha-<short>` to GHCR on main. **Remaining:** pin Helm to immutable **digests** (still `:latest`); gate dep-audit; per-image path filters | Reference digests in Helm; tighten audits |
| 2 | Helm | HIGH | Chart not cluster-functional: TimescaleDB init scripts not mounted (roles/schemas never created), no Spark checkpoint PVCs, no observability stack | Run init scripts via a Job/mount; add PVCs; port the Prometheus/Grafana/Loki stack |
| 3 | Data/DR | HIGH | No TimescaleDB backups or PITR (system of record) | pgBackRest/WAL-G or scheduled `pg_dump` + volume snapshots; document restore |
| 4 | Secrets | HIGH | `CHANGE_ME`/`changeme_*` defaults render into a real Secret; one shared Secret handed to every pod; `pg_hba` allows superuser from `0.0.0.0/0` | Fail deploy on un-overridden placeholders; scope Secrets per service; lock down `pg_hba`, require TLS; stop publishing DB/Kafka/Redis host ports |
| 5 | Containers | HIGH | Most app images run as **root**; no `securityContext` in Helm | Add non-root `USER` to Dockerfiles; restricted `securityContext` (runAsNonRoot, drop caps, readOnlyRootFS) |
| 6 | CI | MED | `spark_jobs`, `ml_service`, `alert_service`, `frontend` tests not run in CI; no lint/dep-audit gate | Expand the test matrix; add ruff/eslint + Dependabot |
| 7 | Observability | MED | Stack is compose-only; no alert rules; ingestion + Spark export no metrics | Add alerting rules; instrument ingestion/Spark; put observability in Helm |
| 8 | Resilience | MED | SPOFs (1× TimescaleDB/Kafka/ZK/Redis/MinIO/Spark worker); no PDB/anti-affinity/HPA | PDBs + anti-affinity; Kafka-lag HPA for stateless consumers; HA path for Kafka/TimescaleDB |
| 9 | Spark | MED | `--master local[*]` in Dockerfile CMD vs `SPARK_MASTER` cluster env (resolved by the job; ambiguous); edge nginx caches upstream IP (static `proxy_pass`) → stale after a service recreate | Make the master one source of truth; give the ingestion edge a `resolver` + variable upstream so it re-resolves |

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
