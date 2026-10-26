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
| `backup.models.enabled` / `prefix` | `true` / `models` | Model mirror of `notebookHub.trackingGateway.artifactBucket`, staging (`uploadStaging.prefix`) excluded |

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
| `MINIO_ENDPOINT` | `http://minio:9000` | `S3_ENDPOINT_URL` and `COLD_SOURCE_ENDPOINT_URL` |
| `BACKUP_S3_BUCKET` | `industryflow-backups` | `S3_BUCKET` |
| `BACKUP_S3_PREFIX` | `timescaledb` | `S3_PREFIX` |
| `BACKUP_S3_REGION` | `us-east-1` | `AWS_DEFAULT_REGION` |
| `BACKUP_DATABASES` | `industryflow mlflow` | `DATABASES` |
| `BACKUP_RETAIN_PER_DB` | `14` | `RETAIN_PER_DB` |
| `COLD_STORE_BUCKET` | `industryflow-cold` | `COLD_SOURCE_BUCKET` |
| `BACKUP_READ_ACCESS_KEY` / `BACKUP_READ_SECRET_KEY` | — | `SOURCE_ACCESS_KEY_ID` / `SOURCE_SECRET_ACCESS_KEY`; must be set, the backup keys cannot read either source |
| `BACKUP_COLD_PREFIX` | `cold` | `COLD_BACKUP_PREFIX` |
| `BACKUP_MODEL_PREFIX` | `models` | `MODEL_BACKUP_PREFIX` (source bucket `mlflow`) |
| `UPLOAD_STAGING_PREFIX` | `_upload-staging/` | `MODEL_SOURCE_EXCLUDE_PREFIX` |
| `BACKUP_NOTEBOOK_PREFIX` | `notebooks` | `notebook-backup`: `NOTEBOOK_BACKUP_PREFIX` |
| `DOCKER_GID` | `999` | `notebook-backup`: the host's docker group |

| Task | Command |
|---|---|
| One backup run | `docker compose run --rm db-backup` |
| Notebook archives | `docker compose --profile backup run --rm notebook-backup` (after `db-backup` has built the image) |
| Without the cold or model mirror | `docker compose run --rm -e COLD_SOURCE_BUCKET= -e MODEL_SOURCE_BUCKET= db-backup` |
| Daily from host cron | `0 2 * * *  cd <install-dir> && docker compose run --rm db-backup && docker compose --profile backup run --rm notebook-backup` |

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
difference from the archive. An existing target is refused.

```bash
# Rehearsal into a scratch volume, then remove it
docker compose --profile backup run --rm --entrypoint notebook-restore.sh notebook-backup \
  industryflow-nb-<user> latest --target industryflow-nb-<user>-rehearsal
docker volume rm industryflow-nb-<user>-rehearsal

# Real restore: the user's server stopped and the volume removed first
docker compose --profile backup run --rm --entrypoint notebook-restore.sh notebook-backup \
  industryflow-nb-<user> latest
```

## Related

| Page | Topic |
|---|---|
| [high availability](high-availability.md) | Replication and HA of the stateful backends |
| [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) | Deleting a tenant from backups |
| [ADR-0034](../../ADR/ADR-0034-platform-schema-migration-strategy.md) | A schema rollback is a restore |
