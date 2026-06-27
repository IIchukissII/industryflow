<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Backup & Disaster Recovery

TimescaleDB is the system of record (sensor measurements, tenants, users, alert/ML metadata)
and the MLflow database backs the model registry. This document describes how they are backed
up and how to recover them.

## Strategy (two tiers)

| Tier | Mechanism | RPO | Status |
|------|-----------|-----|--------|
| **1 — logical backups** | scheduled `pg_dump` of each database → S3/MinIO, with retention | the schedule interval (default 24h) | **implemented** |
| **2 — continuous WAL / PITR** | pgBackRest WAL archiving → S3/MinIO, point-in-time restore | seconds–minutes | **planned** (see below) |

Tier 1 is the baseline and is enough to rebuild the platform to the last scheduled backup.
Tier 2 narrows the recovery point to near-zero and is the recorded next step.

> Backups are only as good as the last restore test. **Rehearse a restore** (into a scratch
> database, see below) on a regular cadence — an unverified backup is not a backup.

## What is backed up

The `db-backup` image (`infrastructure/backup/`, `postgres:15-alpine` + `aws-cli`) runs
`infrastructure/backup/db-backup.sh`, which:

- dumps each database in `DATABASES` (default `industryflow mlflow`) with `pg_dump -Fc -Z6`
  (custom format, compressed, supports selective/parallel restore);
- dumps cluster **globals** (roles) with `pg_dumpall --globals-only`;
- uploads them to `s3://<bucket>/<prefix>/<db>/<UTC-stamp>.dump` (and `…/globals/<stamp>.sql`);
- prunes to the newest `RETAIN_PER_DB` copies per database.

Keys are UTC-timestamped (`YYYYMMDDTHHMMSSZ`) so lexical order is chronological.

### Off-site is what makes it DR

By default the target is the **in-cluster MinIO**, which protects against logical corruption
and accidental drops but **not** against losing the cluster/storage. For genuine disaster
recovery, point the backup at an **off-site / managed S3 bucket** (different failure domain):
override `backup.s3.endpoint` + `backup.s3.bucket` (Helm) or `S3_ENDPOINT_URL` + `S3_BUCKET`
(compose) and supply that bucket's credentials.

## Running backups

### Kubernetes (Helm)

A `CronJob` (`templates/cronjob-db-backup.yaml`) runs the backup on `backup.schedule`
(default `0 2 * * *`, daily 02:00 UTC). It is enabled by default; configure under `backup:` in
`values.yaml`:

```yaml
backup:
  enabled: true
  schedule: "0 2 * * *"
  retainPerDatabase: 14
  s3:
    endpoint: ""            # "" -> in-cluster MinIO; set to an off-site S3 endpoint for real DR
    bucket: industryflow-backups
```

Trigger an immediate run (outside the schedule):

```bash
kubectl create job --from=cronjob/<release>-db-backup db-backup-manual-1
kubectl logs -f job/db-backup-manual-1
```

### Docker Compose

The `db-backup` service is in the `backup` profile, so it does not start with `up`. Run a
one-off backup, or schedule it from a host cron:

```bash
docker compose run --rm db-backup                       # one backup run
# crontab:  0 2 * * *  cd /opt/industryflow && docker compose run --rm db-backup
```

## Restoring

`infrastructure/backup/db-restore.sh` downloads a dump and restores it **TimescaleDB-aware**
(wraps `pg_restore` in `timescaledb_pre_restore()`/`timescaledb_post_restore()`).

> **Destructive:** restoring into an existing database drops and recreates it. Restore the
> **globals first** when rebuilding a fresh cluster, then each database.

### Rehearsal (safe — restore into a scratch database)

```bash
# Compose: restore the latest industryflow backup into a throwaway db, then compare counts.
docker compose run --rm --entrypoint db-restore.sh db-backup \
  industryflow latest --target industryflow_restore_test
```

```bash
# Kubernetes: run the restore tool as a one-off pod (same image/env as the CronJob).
kubectl run db-restore --rm -it --restart=Never \
  --image=ghcr.io/iichukissii/industryflow-db-backup:latest \
  --overrides='{"spec":{"containers":[{"name":"db-restore","image":"ghcr.io/iichukissii/industryflow-db-backup:latest","command":["db-restore.sh","industryflow","latest","--target","industryflow_restore_test"],"envFrom":[{"secretRef":{"name":"<release>-secrets"}}]}]}}'
```

(Adjust env: the script needs `PGHOST PGPORT PGUSER PGPASSWORD S3_BUCKET S3_ENDPOINT_URL
AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY` — the same values the CronJob sets.)

### Full recovery (rebuild a fresh database)

1. Bring up an empty TimescaleDB (the chart's init scripts create the base schema/roles, or
   restore globals to recreate roles).
2. Restore globals: download `…/globals/<stamp>.sql` and apply with `psql`.
3. Restore each database in place:
   ```bash
   docker compose run --rm --entrypoint db-restore.sh db-backup industryflow latest
   docker compose run --rm --entrypoint db-restore.sh db-backup mlflow latest
   ```
4. Verify row counts / recent timestamps per tenant before cutover.

## Tier 2 — continuous WAL archiving / PITR (planned)

To reach a seconds-level RPO and point-in-time recovery, add **pgBackRest** WAL archiving:

- build a TimescaleDB image with `pgbackrest` and set `archive_mode=on` +
  `archive_command='pgbackrest --stanza=industryflow archive-push %p'` in `postgresql.conf`
  (requires a database restart);
- configure a pgBackRest repository on S3/MinIO (`pgbackrest.conf`) and `stanza-create`;
- schedule `pgbackrest backup` (full weekly + diff daily) alongside the WAL stream;
- restore with `pgbackrest restore --type=time --target='<timestamp>'`.

This is deferred (an ADR-0009 deferred decision): it needs a custom DB image, a DB restart,
and validation on a real cluster. Tier 1 covers DR in the meantime.

## See also

- `docs/operations/production-readiness.md` — backlog item 3 (Data/DR).
- `ADR/ADR-0009-kubernetes-deployment-and-packaging.md` — deployment/packaging decisions.
