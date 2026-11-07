<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Backup and disaster recovery

What the backup jobs copy, where to, and how to restore it. Store classification, the failure-domain
rule and restore rehearsal: [ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md); models and
notebooks: [ADR-0038](../../ADR/ADR-0038-model-and-notebook-backup.md).

## Coverage

| Store | Backed up | Mechanism |
|---|---|---|
| Platform database (`industryflow`) | Yes | `pg_dump -Fc -Z6`, scheduled |
| Tracking database (`mlflow`) | Yes | `pg_dump -Fc -Z6`, scheduled |
| Cluster globals (roles) | Yes | `pg_dumpall --globals-only --no-role-passwords` |
| Cold layer ([ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md)) | Yes | Object mirror, same run |
| Model artifacts (artifact bucket, upload staging excluded) | Yes | Object mirror, same run |
| Notebook work volumes ([ADR-0020](../../ADR/ADR-0020-notebook-per-user-persistence.md)), Compose | Yes | `notebook-backup`: one archive per volume |
| Notebook work volumes, Kubernetes | No | The cluster's volume snapshots ([ADR-0038](../../ADR/ADR-0038-model-and-notebook-backup.md) dec 6); not built while the hub has no cluster |
| Notebook hub database | No | Derivable: users and servers return from the platform session |
| Continuous WAL archiving / point-in-time recovery | No | Deferred in [ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md) |

The default target is the deployment's own MinIO: that is undo, not disaster recovery, until the
target is a separate failure domain ([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md)
dec 3; [off-site target](#off-site-target)).

## Recovery objectives

Per authoritative store ([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md) dec 2). A store
counts toward its objectives only while its [restore rehearsal](#restore-rehearsal) passes (dec 6);
`IndustryFlowRestoreRehearsalStale` fires when none has for nine days, and on Compose
`IndustryFlowRestoreRehearsalNeverPassed` when none ever has.

| Store | Recovery point | Recovery time | Proven by |
|---|---|---|---|
| Platform database | One backup interval (`backup.schedule`, default daily) | 1 h | Weekly rehearsal: restore, every table's rows against the rows the dump holds; `industryflow_restore_rehearsal_duration_seconds{database="industryflow"}` |
| Tracking database | One backup interval | 1 h | As above, `database="mlflow"` |
| Cold layer | One backup interval for a newly exported day | Copy-back time of the mirror | Weekly rehearsal: every object present at its size, sampled objects compared byte for byte with the source |
| Model artifacts | One backup interval | Copy-back time of the mirror | As above |
| Notebook volumes (Compose) | One backup interval | Per volume: one `notebook-restore.sh` run | Weekly rehearsal: every volume's latest archive restored and compared entry by entry |

Reference measurement, 4-core 8 GB node, 70 MB platform database: a [full recovery](#full-recovery)
from an empty server (steps 1-5) takes about 30 s; the rehearsal's restore 6-9 s.

## Backup job

Image `infrastructure/backup/` (`postgres:15-alpine` + `aws-cli`, uid 10001), entrypoint
`db-backup.sh`.

| Object | Key |
|---|---|
| Database dump | `s3://<bucket>/<prefix>/<db>/<stamp>.dump` |
| Globals | `s3://<bucket>/<prefix>/globals/<stamp>.sql` |
| Cold mirror | `s3://<bucket>/<cold-prefix>/<source key>` |
| Model mirror | `s3://<bucket>/<model-prefix>/<source key>` |
| Notebook archive | `s3://<bucket>/<notebook-prefix>/<volume>/<stamp>.tar.gz` |

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
| `SKIP_WITHOUT_CREDENTIALS` | unset | `1`: exit 0 with a log line when `PGUSER` is unset; the Helm pre-upgrade hook sets it |
| `PLATFORM_DATABASE` | `industryflow` | A complete run that dumped it adds a row to its `public.backup_ledger`, which the migration runner reads ([before an upgrade](#before-an-upgrade)) |
| `METRICS_TEXTFILE_DIR` | unset | Directory for the run's success time, `industryflow_backup_last_success_timestamp_seconds{backup="databases"}` (node-exporter textfile); Compose sets it |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | required | Backup principal |
| `SOURCE_ENDPOINT_URL` | `S3_ENDPOINT_URL` | Endpoint of the mirrored buckets |
| `SOURCE_ACCESS_KEY_ID` / `SOURCE_SECRET_ACCESS_KEY` | the `AWS_*` pair | Read-only `backup-read` principal |
| `MIRROR_RETAIN_DAYS` | `RETAIN_PER_DB` | Days an object gone from a source stays in its mirror |
| `COLD_SOURCE_BUCKET` | unset (mirror skipped) | Cold bucket to mirror |
| `COLD_SOURCE_ENDPOINT_URL` | `SOURCE_ENDPOINT_URL` | Cold bucket endpoint |
| `COLD_BACKUP_PREFIX` | `cold` | Cold mirror prefix in `S3_BUCKET` |
| `MODEL_SOURCE_BUCKET` | unset (mirror skipped) | Artifact bucket to mirror |
| `MODEL_BACKUP_PREFIX` | `models` | Model mirror prefix in `S3_BUCKET` |
| `MODEL_SOURCE_EXCLUDE_PREFIX` | `_upload-staging/` | Key prefix left out of the model mirror |

### Object mirrors (cold layer, model artifacts)

| Property | Behaviour |
|---|---|
| Incremental | An object is copied when the mirror lacks it or holds a different size; every cold `_manifest.json` is re-copied each run |
| Ageing | An object a source no longer holds stays for `MIRROR_RETAIN_DAYS` after it was first seen missing, then is deleted ([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md) dec 4); first-missing times in `<prefix>/_absent.tsv` |
| Empty source | When a source lists nothing but its mirror holds objects, ageing is skipped for that run |
| Verified | Each copied object's size is read back; a mismatch fails the run |
| Principals | Sources read with `backup-read` (get/list on the artifact and cold buckets, held by the backup job only); mirrors written with the backup principal, which cannot read either source |
| Staging | The model mirror leaves out `MODEL_SOURCE_EXCLUDE_PREFIX` (unadmitted uploads, which expire) |
| Cross-store | Objects stream through the job, so source and target may be different stores |

### Notebook archives (Compose)

Service `notebook-backup` (same image, entrypoint `notebook-backup.sh`, `backup` profile).

| Property | Behaviour |
|---|---|
| Volumes | Every Docker volume whose name starts with the fixed part of `NOTEBOOK_VOLUME_TEMPLATE` (default `industryflow-nb-{username}`, the hub's) |
| Read | Through the Docker Engine API: a container created from `NOTEBOOK_HELPER_IMAGE`, never started, no network, read-only root, the volume mounted read-only; its mount fetched as a tar, gzip'd; the container removed |
| Verified | Archive tested (`gzip -t`, `tar -t`); uploaded size read back |
| Retention | Newest `RETAIN_PER_VOLUME` archives per volume; a volume that no longer exists loses its archives `MIRROR_RETAIN_DAYS` after its newest |
| Consistency | Crash-consistent: a volume in use is archived as it is |
| Holds | The Docker socket (docker group via `DOCKER_GID`) and the backup principal; no database or source-bucket credential |

| Name | Default | Meaning |
|---|---|---|
| `S3_BUCKET`, `S3_ENDPOINT_URL`, `AWS_*` | as the backup job | Target and backup principal |
| `NOTEBOOK_HELPER_IMAGE` | required (`industryflow-db-backup` in Compose) | Image the reading containers are created from |
| `NOTEBOOK_VOLUME_TEMPLATE` | `industryflow-nb-{username}` | The hub's volume name |
| `NOTEBOOK_BACKUP_PREFIX` | `notebooks` | Key prefix in `S3_BUCKET` |
| `RETAIN_PER_VOLUME` | `RETAIN_PER_DB`, else `14` | Archives kept per volume |
| `DOCKER_SOCKET` | `/var/run/docker.sock` | Engine API socket |

### Object-store principal

Against the in-stack MinIO the job uses the `db-backup` principal (read/write/delete on the backup
bucket only), created with the bucket by `infrastructure/minio/minio-init.sh`. It never holds the
MinIO root key.

## Running backups

### Kubernetes (Helm)

CronJob `<release>-db-backup` and pre-upgrade hook Job `<release>-pre-upgrade-backup` (`templates/cronjob-db-backup.yaml`).

| Name | Default | Meaning |
|---|---|---|
| `backup.enabled` | `true` | CronJob rendered |
| `backup.schedule` | `0 2 * * *` | Cron schedule (UTC) |
| `backup.retainPerDatabase` | `14` | `RETAIN_PER_DB` |
| `backup.intervalDays` | `1` | Days between runs of `backup.schedule`; the tenant-delete record's backup expiry |
| `backup.databases` | `industryflow mlflow` | `DATABASES` |
| `backup.s3.endpoint` | `""` (in-cluster MinIO) | `S3_ENDPOINT_URL` |
| `backup.s3.bucket` / `region` / `prefix` | `industryflow-backups` / `us-east-1` / `timescaledb` | Target |
| `backup.cold.enabled` / `prefix` | `true` / `cold` | Cold mirror, only when `coldExport.enabled`; source is `coldExport.store.*` |
| `backup.models.enabled` / `prefix` | `true` / `models` | Model mirror of `notebookHub.trackingGateway.artifactBucket`, staging (`uploadStaging.prefix`) excluded |
| `backup.alertAfterSeconds` | `172800` | `IndustryFlowBackupStale` after this long without a successful run |
| `backup.rehearsal.enabled` | `true` | CronJob `<release>-restore-rehearsal` rendered ([restore rehearsal](#restore-rehearsal)) |
| `backup.rehearsal.schedule` | `0 4 * * 0` | Weekly, after the backup |
| `backup.rehearsal.objectSample` | `20` | `REHEARSAL_OBJECT_SAMPLE` |
| `backup.rehearsal.alertAfterSeconds` | `777600` | `IndustryFlowRestoreRehearsalStale` after this long without a pass |

Credentials come from the `db-backup` workload Secret: `DB_USER`, `DB_PASSWORD`,
`BACKUP_STORE_ACCESS_KEY`, `BACKUP_STORE_SECRET_KEY`, `BACKUP_READ_ACCESS_KEY`, `BACKUP_READ_SECRET_KEY`.

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
| `MINIO_ENDPOINT` | `http://minio:9000` | `S3_ENDPOINT_URL` and `SOURCE_ENDPOINT_URL` (the cold mirror's source unless `COLD_SOURCE_ENDPOINT_URL` is set) |
| `BACKUP_S3_BUCKET` | `industryflow-backups` | `S3_BUCKET` |
| `BACKUP_S3_PREFIX` | `timescaledb` | `S3_PREFIX` |
| `BACKUP_S3_REGION` | `us-east-1` | `AWS_DEFAULT_REGION` |
| `BACKUP_DATABASES` | `industryflow mlflow` | `DATABASES` |
| `BACKUP_RETAIN_PER_DB` | `14` | `RETAIN_PER_DB` |
| `BACKUP_INTERVAL_DAYS` | `1` | Days between backup runs; the tenant-delete record's backup expiry |
| `COLD_STORE_BUCKET` | `industryflow-cold` | `COLD_SOURCE_BUCKET` |
| `BACKUP_READ_ACCESS_KEY` / `BACKUP_READ_SECRET_KEY` | — | `SOURCE_ACCESS_KEY_ID` / `SOURCE_SECRET_ACCESS_KEY`; must be set, the backup keys cannot read either source |
| `BACKUP_COLD_PREFIX` | `cold` | `COLD_BACKUP_PREFIX` |
| `BACKUP_MODEL_PREFIX` | `models` | `MODEL_BACKUP_PREFIX` (source bucket `mlflow`) |
| `UPLOAD_STAGING_PREFIX` | `_upload-staging/` | `MODEL_SOURCE_EXCLUDE_PREFIX` |
| `BACKUP_NOTEBOOK_PREFIX` | `notebooks` | `notebook-backup`: `NOTEBOOK_BACKUP_PREFIX` |
| `DOCKER_GID` | `999` | `notebook-backup`: the host's docker group |
| `BACKUP_REHEARSAL_OBJECT_SAMPLE` | `20` | `restore-rehearsal`: `REHEARSAL_OBJECT_SAMPLE` |

| Task | Command |
|---|---|
| One backup run | `docker compose run --rm --no-deps db-backup` |
| Notebook archives | `docker compose --profile backup run --rm --no-deps notebook-backup` (after `db-backup` has built the image) |
| Without the cold or model mirror | `docker compose run --rm --no-deps -e COLD_SOURCE_BUCKET= -e MODEL_SOURCE_BUCKET= db-backup` |
| Daily from host cron | `0 2 * * *  cd <install-dir> && docker compose run --rm --no-deps db-backup && docker compose --profile backup run --rm --no-deps notebook-backup` |
| Weekly rehearsal from host cron | `0 4 * * 0  cd <install-dir> && { docker compose --profile backup run --rm --no-deps restore-rehearsal; docker compose --profile backup run --rm --no-deps notebook-rehearsal; }` |

Each job writes its success time to the `backup-metrics` volume, which node-exporter exposes;
`IndustryFlowBackupStale` and `IndustryFlowRestoreRehearsalStale` fire when they stop,
`IndustryFlowBackupNeverCompleted` and `IndustryFlowRestoreRehearsalNeverPassed` when they never ran
([`compose.yml`](../../infrastructure/prometheus/rules/compose.yml)).

### Before an upgrade

`migrate.sh apply` refuses pending scripts on a database holding a tenant or a user unless a backup is
recorded in its `public.backup_ledger` within `MIGRATE_BACKUP_MAX_AGE_SECONDS`
([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md) dec 7;
[runner](../../infrastructure/timescaledb/init-scripts/README.md#backup-before-an-upgrade)).

| Deployment | Backup taken by | Without a backup |
|---|---|---|
| Compose | `docker compose run --rm --no-deps db-backup`, then `docker compose up db-migrate`; `scripts/box-deploy.sh` runs both in that order (ADR-0032 dec 7) | `MIGRATE_WITHOUT_BACKUP=1 docker compose up db-migrate` |
| Helm | Hook Job `<release>-pre-upgrade-backup` (`helm.sh/hook: pre-upgrade`), before the revision's `db-migrate` Job: the database dumps only, no object mirrors; `helm upgrade --timeout` must cover them. Skipped, with a log line, on the upgrade that sets `backup.enabled=true` (its Secret and principal do not exist yet) | `dbMigrations.withoutBackup=true`; required when `backup.enabled=false`, the chart refuses to render otherwise |

Pre-upgrade dumps are ordinary runs: they count toward `RETAIN_PER_DB`.

### Off-site target

| Deployment | Setting |
|---|---|
| Helm | `backup.s3.endpoint`, `backup.s3.bucket`, and that bucket's keys as `BACKUP_STORE_ACCESS_KEY` / `BACKUP_STORE_SECRET_KEY` |
| Compose | `BACKUP_S3_BUCKET` and `BACKUP_STORE_*` in `.env`; the endpoint per run: `docker compose run --rm --no-deps -e S3_ENDPOINT_URL=https://<s3-endpoint> db-backup` (the service otherwise uses `MINIO_ENDPOINT`) |

## Restore rehearsal

Scheduled proof that the latest backup restores ([ADR-0032](../../ADR/ADR-0032-backup-and-disaster-recovery.md)
dec 6). Compose: services `restore-rehearsal` and `notebook-rehearsal`; Helm: CronJob
`<release>-restore-rehearsal`.

| Step | `db-rehearse.sh` |
|---|---|
| 1 | Per database, the newest dump's rows are counted per table from the dump itself (`pg_restore -a`, the lines of each COPY block) |
| 2 | The dump is restored by `db-restore.sh --hold-jobs` into `<db>_rehearsal` on `PGHOST`, the restored TimescaleDB policies unscheduled, and read back the same way (`pg_dump -a`) |
| 3 | Every table must match: a dumped table missing or with another count fails, as does a restored table with rows the dump does not hold. Compared per physical table (chunks and compressed chunks included) and TimescaleDB's structural catalog (hypertables, chunks, dimensions and slices, chunk constraints, continuous aggregates); what its scheduler writes after a restore and its job configuration are not |
| 4 | The scratch database is dropped, also after a failure |
| 5 | Per object mirror: every source object last modified before the rehearsed backup run started and missing from the mirror or held there at another size fails, named; `REHEARSAL_OBJECT_SAMPLE` objects held at the same size are read from both and compared by SHA-256. Cold manifests (rewritten in place) are neither compared nor sampled |
| 6 | On a pass: `industryflow_restore_rehearsal_last_success_timestamp_seconds{backup="databases"}` and `industryflow_restore_rehearsal_duration_seconds{database}` |

| Step | `notebook-rehearse.sh` (Compose) |
|---|---|
| 1 | Per volume with an archive, the latest is restored by `notebook-restore.sh` into `rehearsal-<volume>` and compared with the archive entry by entry |
| 2 | The scratch volume is removed; a leftover from an interrupted run is replaced |
| 3 | On a pass with at least one volume: `industryflow_restore_rehearsal_last_success_timestamp_seconds{backup="notebooks"}` |

| Name | Default | Meaning |
|---|---|---|
| `REHEARSAL_SUFFIX` | `_rehearsal` | Scratch database suffix; empty is refused |
| `REHEARSAL_OBJECT_SAMPLE` | `20` | Objects compared per mirror |
| `REHEARSAL_VOLUME_PREFIX` | `rehearsal-` | Scratch volume prefix; empty, or one starting with the work-volume prefix, is refused |
| `PGHOST` | the backup's | Server restored into; it briefly holds a second copy of each database. A scratch server holding the [globals](#full-recovery) keeps that load off production |

The rehearsal proves a backup restores to exactly the rows it holds; it does not compare the backup
with the live database.

## Restoring

`db-restore.sh <database> [<stamp>|latest] [--target <db>] [--hold-jobs]` (same image) downloads one
dump (`latest`: the newest `.dump`) and:

1. refuses, before touching the target, when the dump names a role the cluster lacks or `PGUSER`
   is not a superuser;
2. drops and recreates the target database (`DROP DATABASE … WITH (FORCE)`) with the dumped owner:
   **destructive**;
3. for a dump that contains `timescaledb`: creates the extension and calls
   `timescaledb_pre_restore()`;
4. runs `pg_restore`, keeping owners and privileges;
5. with `--hold-jobs`, unschedules the restored TimescaleDB policies (for a rehearsal copy, never a
   cutover); calls `timescaledb_post_restore()`, whatever the result;
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
docker compose run --rm --no-deps --entrypoint db-restore.sh db-backup \
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
| 3 | `docker compose run --rm --no-deps --entrypoint db-restore.sh db-backup industryflow latest` |
| 4 | `docker compose run --rm --no-deps --entrypoint db-restore.sh db-backup mlflow latest` |
| 5 | `docker compose run --rm --no-deps db-backup`, then `docker compose up db-migrate`: the restored ledger is the backup's; scripts newer than the backup are applied ([ADR-0034](../../ADR/ADR-0034-platform-schema-migration-strategy.md) dec 5) after a backup of the restored state ([before an upgrade](#before-an-upgrade)) |
| 6 | Verify row counts and recent timestamps per tenant before cutover |

### Cold layer

With the cold exporter stopped, copy the mirror back with the same keys:

```bash
aws s3 sync s3://<backup-bucket>/cold/ s3://<cold-bucket>/
```

Manifests return with their parts, so readers see the same verified days.

### Model artifacts

With the tracking gateway stopped, copy the mirror back with the same keys:

```bash
aws s3 sync s3://<backup-bucket>/models/ s3://<artifact-bucket>/ --exclude '_absent.tsv'
```

Rehearsal: sync into a scratch bucket and compare `aws s3 ls --recursive` listings (key, size) with the
source.

### Notebook volumes (Compose)

`notebook-restore.sh <volume> [<stamp>|latest] [--target <volume>]` creates the target volume, writes the
archive into it through the Engine API (owners and modes as archived), reads it back and fails on any
difference from the archive in an entry's name, mode, owner or size. An existing target is refused.

```bash
# Rehearsal into a scratch volume (a name outside the work-volume prefix), then remove it
docker compose --profile backup run --rm --no-deps --entrypoint notebook-restore.sh notebook-backup \
  industryflow-nb-<user> latest --target rehearsal-industryflow-nb-<user>
docker volume rm rehearsal-industryflow-nb-<user>

# Real restore: the user's server stopped and the volume removed first
docker compose --profile backup run --rm --no-deps --entrypoint notebook-restore.sh notebook-backup \
  industryflow-nb-<user> latest
```

## ADR-0032 dec 3-7

The backup, restore and rehearsal jobs copy every authoritative store to a separate target and prove the restore.

| Code | Role |
|---|---|
| `infrastructure/backup` | Backup, restore and rehearsal scripts |

## ADR-0038 dec 1-3

The backup job mirrors model artifacts with the one read-only principal, leaving the staging area out.

| Code | Role |
|---|---|
| `infrastructure/backup/db-backup.sh` | Object mirrors |
| `infrastructure/minio/minio-init.sh` | `backup-read` principal |

## ADR-0038 dec 4, 5

Each authored work volume is archived and verified per run.

| Code | Role |
|---|---|
| `infrastructure/backup/notebook-backup.sh` | Volume archives |
| `infrastructure/backup/notebook-restore.sh` | Volume restore |

## ADR-0038 dec 8

| Kind | Rehearsal | Compared | Fails when |
|---|---|---|---|
| Model mirror | `db-rehearse.sh`, after the database rehearsal | Every source object (staging area excluded) last modified before the rehearsed backup run started, against the mirror: present at the same size; bytes (SHA-256) of a random sample of `REHEARSAL_OBJECT_SAMPLE` objects (default 20) | A source object is missing from the mirror or differs in size or sampled bytes, each named |
| Work-volume archive | `notebook-rehearse.sh`: each volume's latest archive restored into a scratch volume `rehearsal-<volume>`, then removed | Every entry's path, type, size, owner and mode against the archive | Any entry differs; the scratch prefix names a work volume |

| Code | Role |
|---|---|
| `infrastructure/backup/db-rehearse.sh` | `rehearse_mirror`: model and cold mirrors |
| `infrastructure/backup/notebook-rehearse.sh` | `rehearse_main`: volume rehearsal |
| `infrastructure/backup/notebook-restore.sh` | Restore into a target; read back and compare |
| `docker-compose.yml` | `notebook-rehearsal` service |
| `infrastructure/backup/tests/test_backup_scripts.py::test_a_mirrored_object_with_other_bytes_fails_the_rehearsal` | Proof: a differing object fails |
| `infrastructure/backup/tests/test_backup_scripts.py::test_every_source_object_missing_from_the_mirror_fails_the_rehearsal_named` | Proof: an object missing from the mirror fails, named |
| `infrastructure/backup/tests/test_notebook_backup.py::test_a_rehearsal_restores_every_volumes_latest_archive_into_scratch_and_removes_it` | Proof: volume rehearsal |
| `infrastructure/backup/tests/test_notebook_backup.py::test_a_restore_that_wrote_less_than_the_archive_fails` | Proof: a short restore fails |

## Related

| Page | Topic |
|---|---|
| [high availability](high-availability.md) | Replication and HA of the stateful backends |
| [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) | Deleting a tenant from backups |
| [ADR-0034](../../ADR/ADR-0034-platform-schema-migration-strategy.md) | A schema rollback is a restore |
