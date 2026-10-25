<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Backup and disaster recovery

What the backup job copies, where to, and how to restore it. Store classification, the failure-domain
rule and restore rehearsal: [ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md).

## Coverage

| Store | Backed up | Mechanism |
|---|---|---|
| Platform database (`industryflow`) | Yes | `pg_dump -Fc -Z6`, scheduled |
| Tracking database (`mlflow`) | Yes | `pg_dump -Fc -Z6`, scheduled |
| Cluster globals (roles) | Yes | `pg_dumpall --globals-only --no-role-passwords` |
| Cold layer ([ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md)) | Yes | Object mirror, same run |
| Model artifacts (object store) | No | — |
| Notebook volumes ([ADR-0020](../../ADR/ADR-0020-notebook-per-user-persistence.md)) | No | — |
| Continuous WAL archiving / point-in-time recovery | No | Deferred in [ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md) |

The recovery point of the databases is the schedule interval. The default target is the deployment's
own MinIO: that is undo, not disaster recovery, until the target is a separate failure domain
([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md) dec 3; [off-site target](#off-site-target)).

## Backup job

Image `infrastructure/backup/` (`postgres:15-alpine` + `aws-cli`, uid 10001), entrypoint
`db-backup.sh`.

| Object | Key |
|---|---|
| Database dump | `s3://<bucket>/<prefix>/<db>/<stamp>.dump` |
| Globals | `s3://<bucket>/<prefix>/globals/<stamp>.sql` |
| Cold mirror | `s3://<bucket>/<cold-prefix>/<source key>` |

`<stamp>` is UTC `YYYYMMDDTHHMMSSZ`; lexical order is chronological. Dumps and globals are pruned to
the newest `RETAIN_PER_DB` per prefix. The bucket is created if absent.

| Name | Default | Meaning |
|---|---|---|
| `PGHOST` / `PGPORT` / `PGUSER` / `PGPASSWORD` | required (`PGPORT` `5432`) | Database connection (TLS via `PGSSLMODE` / `PGSSLROOTCERT`) |
| `S3_BUCKET` | required | Backup bucket |
| `S3_ENDPOINT_URL` | unset (AWS S3) | S3 endpoint |
| `S3_PREFIX` | `timescaledb` | Key prefix for dumps and globals |
| `DATABASES` | `industryflow mlflow` | Databases to dump |
| `RETAIN_PER_DB` | `14` | Copies kept per database |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | required | Backup principal |
| `COLD_SOURCE_BUCKET` | unset (mirror skipped) | Cold bucket to mirror |
| `COLD_SOURCE_ENDPOINT_URL` | `S3_ENDPOINT_URL` | Cold bucket endpoint |
| `COLD_SOURCE_ACCESS_KEY_ID` / `COLD_SOURCE_SECRET_ACCESS_KEY` | the `AWS_*` pair | Read-only cold principal |
| `COLD_BACKUP_PREFIX` | `cold` | Mirror prefix in `S3_BUCKET` |
| `COLD_MIRROR_RETAIN_DAYS` | `RETAIN_PER_DB` | Days an object gone from the source stays in the mirror |

### Cold mirror

| Property | Behaviour |
|---|---|
| Incremental | An object is copied when the mirror lacks it or holds a different size; every `_manifest.json` is re-copied each run |
| Ageing | An object the source no longer holds stays for `COLD_MIRROR_RETAIN_DAYS` after it was first seen missing, then is deleted ([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md) dec 4); first-missing times in `<cold-prefix>/_absent.tsv` |
| Empty source | When the source lists nothing but the mirror holds objects, ageing is skipped for that run |
| Verified | Each copied object's size is read back; a mismatch fails the run |
| Principals | Source read with the read-only cold principal (`COLD_READ_ACCESS_KEY`); mirror written with the backup principal, which cannot read the cold bucket |
| Cross-store | Objects stream through the job, so source and target may be different stores |

### Object-store principal

Against the in-stack MinIO the job uses the `db-backup` principal (read/write/delete on the backup
bucket only), created with the bucket by `infrastructure/minio/minio-init.sh`. It never holds the
MinIO root key.

## Running backups

### Kubernetes (Helm)

CronJob `<release>-db-backup` (`templates/cronjob-db-backup.yaml`).

| Name | Default | Meaning |
|---|---|---|
| `backup.enabled` | `true` | CronJob rendered |
| `backup.schedule` | `0 2 * * *` | Cron schedule (UTC) |
| `backup.retainPerDatabase` | `14` | `RETAIN_PER_DB` |
| `backup.databases` | `industryflow mlflow` | `DATABASES` |
| `backup.s3.endpoint` | `""` (in-cluster MinIO) | `S3_ENDPOINT_URL` |
| `backup.s3.bucket` / `region` / `prefix` | `industryflow-backups` / `us-east-1` / `timescaledb` | Target |
| `backup.cold.enabled` / `prefix` | `true` / `cold` | Cold mirror, only when `coldExport.enabled`; source is `coldExport.store.*` |

Credentials come from the `db-backup` workload Secret: `DB_USER`, `DB_PASSWORD`,
`BACKUP_STORE_ACCESS_KEY`, `BACKUP_STORE_SECRET_KEY`, `COLD_READ_ACCESS_KEY`, `COLD_READ_SECRET_KEY`.

```bash
kubectl -n <namespace> create job --from=cronjob/<release>-db-backup db-backup-manual-1
kubectl -n <namespace> logs -f job/db-backup-manual-1
```

### Docker Compose

`db-backup` is in the `backup` profile and does not start with `up`. Each `.env` variable sets the
job variable named in *Meaning*.

| Name (`.env`) | Default | Meaning |
|---|---|---|
| `DB_USER` / `DB_PASSWORD` | — | `PGUSER` / `PGPASSWORD` |
| `BACKUP_STORE_ACCESS_KEY` / `BACKUP_STORE_SECRET_KEY` | — | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` |
| `MINIO_ENDPOINT` | `http://minio:9000` | `S3_ENDPOINT_URL` and `COLD_SOURCE_ENDPOINT_URL` |
| `BACKUP_S3_BUCKET` | `industryflow-backups` | `S3_BUCKET` |
| `BACKUP_S3_PREFIX` | `timescaledb` | `S3_PREFIX` |
| `BACKUP_S3_REGION` | `us-east-1` | `AWS_DEFAULT_REGION` |
| `BACKUP_DATABASES` | `industryflow mlflow` | `DATABASES` |
| `BACKUP_RETAIN_PER_DB` | `14` | `RETAIN_PER_DB` |
| `COLD_STORE_BUCKET` | `industryflow-cold` | `COLD_SOURCE_BUCKET` |
| `COLD_READ_ACCESS_KEY` / `COLD_READ_SECRET_KEY` | — | Cold source keys; must be set, the backup keys cannot read the cold bucket |
| `BACKUP_COLD_PREFIX` | `cold` | `COLD_BACKUP_PREFIX` |

| Task | Command |
|---|---|
| One backup run | `docker compose run --rm db-backup` |
| Without the cold mirror | `docker compose run --rm -e COLD_SOURCE_BUCKET= db-backup` |
| Daily from host cron | `0 2 * * *  cd <install-dir> && docker compose run --rm db-backup` |

### Off-site target

| Deployment | Setting |
|---|---|
| Helm | `backup.s3.endpoint`, `backup.s3.bucket`, and that bucket's keys as `BACKUP_STORE_ACCESS_KEY` / `BACKUP_STORE_SECRET_KEY` |
| Compose | `BACKUP_S3_BUCKET` and `BACKUP_STORE_*` in `.env`; the endpoint per run: `docker compose run --rm -e S3_ENDPOINT_URL=https://<s3-endpoint> db-backup` (the service otherwise uses `MINIO_ENDPOINT`) |

## Restoring

`db-restore.sh <database> [<stamp>|latest] [--target <db>]` (same image) downloads one dump and:

1. refuses, before touching the target, when the dump names a role the cluster lacks or `PGUSER`
   is not a superuser;
2. drops and recreates the target database (`DROP DATABASE … WITH (FORCE)`) with the dumped owner:
   **destructive**;
3. for a dump that contains `timescaledb`: creates the extension and calls
   `timescaledb_pre_restore()`;
4. runs `pg_restore`, keeping owners and privileges;
5. calls `timescaledb_post_restore()`, whatever the result;
6. applies the database's own grants and settings to the target.

| Restored as dumped | Includes |
|---|---|
| Owners | tables, schemas, functions (a `SECURITY DEFINER` function keeps its owner), the database |
| Privileges | table, column, sequence, schema, function and database grants; default privileges |
| Settings | per-database settings (also with `--target`) |

It exits non-zero on any `pg_restore` error other than the expected `timescaledb` extension
collision, or when no statement error was reported but `pg_restore` failed. A failed restore is
incomplete; do not cut over to it.

### Rehearsal into a scratch database

```bash
# Compose
docker compose run --rm --entrypoint db-restore.sh db-backup \
  industryflow latest --target industryflow_restore_test

# Kubernetes: a Job from the backup CronJob (same env, Secret and CA mount), command replaced
kubectl -n <namespace> create job --from=cronjob/<release>-db-backup db-restore-test \
  --dry-run=client -o json \
  | jq '.spec.template.spec.containers[0].command = ["db-restore.sh","industryflow","latest","--target","industryflow_restore_test"]' \
  | kubectl -n <namespace> apply -f -
kubectl -n <namespace> logs -f job/db-restore-test
```

Compare row counts and recent timestamps per tenant with the source, then drop the scratch database.

### Full recovery

| Step | Action |
|---|---|
| 1 | Start an empty TimescaleDB and `docker compose up db-migrate`: the schema sequence creates the roles (passwords from the environment) and base schema |
| 2 | Apply `<prefix>/globals/<stamp>.sql` with `psql`: it recreates the runtime roles (`tenant_reader_*`, `tenant_sqlproxy_*`); `role … already exists` errors for the service roles are expected. Roles carry no passwords (`--no-role-passwords`) |
| 3 | `docker compose run --rm --entrypoint db-restore.sh db-backup industryflow latest` |
| 4 | `docker compose run --rm --entrypoint db-restore.sh db-backup mlflow latest` |
| 5 | `docker compose up db-migrate`: the restored ledger is the backup's; scripts newer than the backup are applied ([ADR-0034](../../ADR/ADR-0034-platform-schema-migration-strategy.md) dec 5) |
| 6 | Verify row counts and recent timestamps per tenant before cutover |

### Cold layer

With the cold exporter stopped, copy the mirror back with the same keys:

```bash
aws s3 sync s3://<backup-bucket>/cold/ s3://<cold-bucket>/
```

Manifests return with their parts, so readers see the same verified days.

## Related

| Page | Topic |
|---|---|
| [high availability](high-availability.md) | Replication and HA of the stateful backends |
| [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) | Deleting a tenant from backups |
| [ADR-0034](../../ADR/ADR-0034-platform-schema-migration-strategy.md) | A schema rollback is a restore |
