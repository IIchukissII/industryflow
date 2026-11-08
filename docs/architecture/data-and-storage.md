<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Data and storage

The manual of [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md), [ADR-0025](../../ADR/ADR-0025-cold-layer-for-historical-telemetry-in-an-open-columnar-format.md), [ADR-0039](../../ADR/ADR-0039-one-test-of-tenant-ownership-for-names-and-object-store-keys.md): where tenant data lives (PostgreSQL + TimescaleDB with one schema per tenant, and the cold layer in the object store) and how a name or key is tied to its tenant.

## ADR-0003 dec 1-3

| Caller | Tenant taken from |
|---|---|
| Gateway request | The authenticated user's `company_id`, read with the user by primary key |
| ML service request | The `company_id` claim the gateway signs into the access token; a token without a UUID claim answers 401 |
| Device reading | The URI SAN of the client certificate the edge verified, stamped on the reading by ingestion ([ADR-0002](../../ADR/ADR-0002-ingestion-authentication-and-device-identity.md)) |
| Spark, alert worker | The Kafka message (stamped by ingestion) |
| Notebook kernel | The capability handle ([notebooks](notebooks.md)) |

A request body's `company_id` is not the tenant for a user caller. A caller presenting `X-Internal-Service-Token` names the tenant in the body's `company_id`, validated as a UUID. The user self-update schema refuses a tenant field.

| Code | Role |
|---|---|
| `services/api_gateway/users.py::CompanyClaimJWTStrategy` | Signs `company_id` into the access token |
| `services/api_gateway/dependencies.py::get_current_user_with_company` | Gateway tenant from the authenticated user |
| `services/api_gateway/schemas.py::UserSelfUpdate` | No tenant field on self-update |
| `services/api_gateway/routers/user_admin.py` | User administration inside the caller's tenant |
| `services/ml_service/api/auth.py::company_id_of` | ML service tenant from the token claim |
| `services/ml_service/api/routers/inference.py::_resolve_company_id` | Internal-service caller names the tenant |
| `services/ingestion_service/dependencies.py::get_ingestion_identity` | Device tenant from the verified certificate |
| `services/ml_service/tests/test_tenant_from_token.py` | Proof: the claim is the tenant, a body `company_id` is not |
| `services/api_gateway/tests/test_user_admin.py` | Proof: self-update and administration stay in the caller's tenant |

Provisioning: [user management](../operations/user-management.md#tenants).

## ADR-0003 dec 4, 5, 7

Every function of the library parses the tenant identifier as a UUID and builds names from its canonical string; a non-UUID raises `ValueError`. Every image is built with that library.

| Code | Role |
|---|---|
| `libs/industryflow_contracts/industryflow_contracts/tenancy.py::canonical_company_id` | UUID validation and canonical form |
| `libs/industryflow_contracts/industryflow_contracts/tenancy.py::schema_name` | The canonical tenant-to-schema function |
| `libs/industryflow_contracts/tests/test_tenancy.py` | Proof: a non-UUID never becomes a name; every spelling of one tenant yields one name |
| `tests/config_guards/test_tenant_contract_copies.py` | No deployable keeps its own copy of the contract |
| `tests/config_guards/test_images_carry_contracts.py` | Every image that imports the library is built with it |

### Tenant names

All tenant-derived names come from `libs/industryflow_contracts` (`industryflow_contracts.tenancy`).
Every service image installs it (named build context `contracts`); the Spark jobs ship it to
executors with `--py-files`.

| Name | Function | Form |
|---|---|---|
| Schema | `schema_name` | `tenant_<uuid with underscores>` |
| Read-only role | `reader_role_name` | `tenant_reader_<uuid with underscores>` |
| SQL-proxy login | `sql_login_name` | `tenant_sqlproxy_<uuid with underscores>` |
| MLflow names | `mlflow_name_prefix` | `tenant_<uuid with underscores>.` |
| MLflow artifacts, admitted uploads | `mlflow_artifact_prefix` | `tenant_<uuid with underscores>/` |
| Cold archive | `cold_object_prefix` | `tenant_<uuid>` (hyphens) |
| Schema → tenant | `company_id_from_schema` | inverse of `schema_name` |
| Catalog filter | `SCHEMA_LIKE` | `tenant\_%` |

| Guard | Fails on |
|---|---|
| `tests/config_guards/test_tenant_contract_copies.py` | A service or script spelling a tenant name (ADR-0003 dec 7) or restating the window contract (ADR-0006 dec 7) in code |
| `tests/config_guards/test_images_carry_contracts.py` | An image whose code imports the package but does not install it (ADR-0003 dec 7) |

### Tables

`public` schema:

| Table | Content |
|---|---|
| `companies` | `company_id` → `schema_name` registry |
| `user` | Platform users (fastapi-users; `29-user-table.sql`, ADR-0034 dec 1; mapped by `services/api_gateway/models/user.py`) |
| `platform_config` | Platform switches (`stateful_features_enabled`, [ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)) |
| `cold_export_drops` | Committed cold-export drops |
| `company_lifecycle_events`, `company_deletions`, `company_deletion_steps` | Tenant lifecycle record and deletion progress ([ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md); `25-company-deletion.sql`) |
| `capability_use_events` | Capability-use record, append-only, outlives tenants ([ADR-0042](../../ADR/ADR-0042-capability-use-is-recorded-by-the-planes-that-resolve-it-into-one-durable-record.md); [record](notebooks.md#capability-use-record)) |
| `schema_ledger` | Applied schema scripts and digests (`migrate.sh`, [ADR-0034](../../ADR/ADR-0034-platform-schema-migrations.md)) |

Per tenant schema, created by `create_tenant_schema()`:

| Table | Hypertable on, chunk | Key | Compression (segment by, after) | Retention |
|---|---|---|---|---|
| `equipment` | | | | |
| `sensors` | | `UNIQUE (equipment_id, sensor_name)` | | |
| `sensor_measurements` | `time`, 1 d | `UNIQUE (time, sensor_id)`; index `(sensor_id, time DESC)` | `equipment_id`, 7 d | None; the cold export drops it |
| `sensor_aggregations_1min` | `time`, 7 d | `UNIQUE (time, sensor_id, equipment_id)` | `equipment_id`, 14 d | 90 d |
| `sensor_aggregations_5min` | `time`, 30 d | same | `equipment_id`, 30 d | 180 d |
| `sensor_aggregations_1hour` | `time`, 90 d | same | `equipment_id`, 60 d | 1 y |
| `alert_rules` | | | | |
| `alerts` | `triggered_at`, 7 d | | | 90 d |
| `alert_labels` | | | | |
| `ml_models` | | `UNIQUE (model_name, model_version)`; `feature_config_id` → `feature_engineering_configs` `ON DELETE RESTRICT` | | |
| `model_predictions` | `timestamp`, 7 d | | `model_id`, 30 d | 180 d |
| `feature_engineering_configs` | | A config a model binds keeps `base_sensors`, `transformations` ([ADR-0041](../../ADR/ADR-0041-a-model-s-feature-configuration-is-fixed-once-bound.md)) | | |

`sensors` naming columns:

| Column | Type | Meaning |
|---|---|---|
| `sensor_name` | `VARCHAR(100) NOT NULL` | Machine key; referenced by feature configs, model feature names and alert-rule `sensor_pattern`; not editable through the API |
| `display_name` | `VARCHAR(100)`, nullable | Human label (`22-sensor-display-name.sql`); NULL shows `sensor_name` |

Payloads that name a sensor carry both: `GET /api/equipment/{id}/sensors`, `GET /api/cache/sensors`,
WS `sensor_update` (`sensor_name`, `display_name`); `GET /api/alerts` and `GET /api/alert-rules`
(`sensor_name`, `sensor_display_name`, joined at read time). The frontend shows
`sensorLabel()` (`services/frontend/src/utils/sensors.js`).

Extension-owned domain tables: [extensions](../operations/extensions.md).

## ADR-0003 dec 6

Each tenant-scoped query runs in a transaction that sets the path with `SET LOCAL search_path` or `set_config('search_path', …, true)`; the connection returns to the pool without it.

| Code | Role |
|---|---|
| `services/api_gateway/dependencies.py::get_db_with_tenant` | Gateway session per user |
| `services/api_gateway/dependencies.py::get_tenant_db` | Gateway session per session or capability |
| `services/ml_service/api/repository.py::tenant_connection` | ML service pooled connection |
| `services/alert_service/worker/repository.py::AlertRepository` | Alert worker pooled connection (`_tenant`) |
| `services/ml_service/api/feature_engineering/baseline_provider.py::AggregateBaselineProvider` | Baseline read |
| `services/ml_service/tests/test_silent_omission_guards.py::test_nothing_in_the_service_sets_a_session_scoped_search_path` | Proof: no session-scoped path in the ML service |
| `services/alert_service/tests/test_repository_sql.py::test_search_path_is_only_ever_set_transaction_locally` | Proof: alert worker |
| `infrastructure/timescaledb/tests/test_alert_dedup_live.py` | Proof: no tenant path survives the checkout on a real database |

## ADR-0025 dec 1

Raw readings are kept as Parquet (format 2.6, zstd) in the cold bucket; no expiry applies to it. Notebook readers fetch the files directly with pre-signed reads.

| Code | Role |
|---|---|
| `services/cold_export/store.py::PARQUET_FORMAT_VERSION` | Parquet encoding contract |
| `clients/python/industryflow/cold.py::IndustryFlowCold` | Reads a tenant's Parquet as DataFrames |
| `services/cold_export/tests/test_parquet_compat.py` | Proof: archived files still read across a pyarrow upgrade |

## ADR-0025 dec 2

`sensor_measurements` has no retention policy; the cold exporter drops a day past the horizon (`COLD_EXPORT_HORIZON_DAYS`, default 30) after its export.

| Code | Role |
|---|---|
| `infrastructure/timescaledb/init-scripts/04-complete-tenant-creation.sql::create_base_tables` | Raw table without a retention policy |
| `infrastructure/timescaledb/init-scripts/16-cold-layer-raw-retention.sql::ensure_cold_export_access` | Removes a raw retention policy on existing tenants |
| `services/cold_export/config.py::load_config` | Export horizon |

## ADR-0025 dec 3, 4, 11

The exporter writes, verifies and only then drops; it rewrites no archived part.

| Code | Role |
|---|---|
| `services/cold_export/exporter.py::run_export` | Orders export, verify and drop per tenant and day |
| `services/cold_export/exporter.py::export_day` | One day: export a part, verify it, record and drop |
| `services/cold_export/source.py::PostgresMeasurementSource` | Database reads and the run lock |
| `services/cold_export/store.py::S3ColdStore` | Object-store writes and reads |
| `services/cold_export/ports.py` | The `MeasurementSource` and `ColdStore` ports |
| `infrastructure/timescaledb/init-scripts/16-cold-layer-raw-retention.sql::cold_export_drop_day` | Fingerprint re-check, drop and drop record |
| `services/cold_export/tests/test_exporter.py` | Proof: export, verify and drop ordering |
| `services/cold_export/tests/test_cold_export_integration.py` | Proof: on a real database and object store |

### Cold layer

Raw `sensor_measurements` older than the export horizon, as Parquet in the object store.

| Component | Where |
|---|---|
| Exporter (single-node PyArrow; ADR-0025 dec 3, 4, 11) | `services/cold_export/` — `exporter.py` (ordering), `source.py` (database reads, run lock), `store.py` (object store), `naming.py` (paths), `ports.py` |
| Schedule | Helm `cronjob-cold-export.yaml` (`coldExport.schedule`, `concurrencyPolicy: Forbid`); compose `docker compose --profile cold-export up cold-export` |
| Horizon | `COLD_EXPORT_HORIZON_DAYS` / `coldExport.horizonDays`, default 30 |
| Bucket | `COLD_STORE_BUCKET` / `coldExport.store.bucket`, default `industryflow-cold` |
| Database side | `16-cold-layer-raw-retention.sql`: `cold_export_day_fingerprint`, `cold_export_drop_day` (`SECURITY DEFINER`), `public.cold_export_drops` |
| Object-store principals | Write-scoped exporter principal; read-only broker principal; created by `minio-init` / Helm `job-minio-init.yaml` |
| Read path (ADR-0025 dec 5) | `services/notebook_cold_broker/`, client `industryflow.IndustryFlowCold` ([notebooks](notebooks.md)) |
| Backup | `db-backup` mirrors the cold and artifact buckets (`backup-read` principal; Helm `backup.cold`, `backup.models`); `notebook-backup` archives work volumes; `restore-rehearsal` and `notebook-rehearsal` restore the latest backups into scratch copies weekly ([backup and recovery](../operations/backup-and-recovery.md)) |

Exporter behaviour per tenant and day:

| Step | Detail |
|---|---|
| Run lock | Session advisory lock for the whole run; a run that finds it held exits 0 |
| Export | Day read from one snapshot, written as a part: `measurements.parquet`, then `measurements-0001.parquet`, … for rows that arrive later |
| Verify | Written row count reconciled against the source; part listed in the day's `_manifest.json` |
| Drop | `cold_export_drop_day` re-checks the day's fingerprint (count + digest) under a lock and drops nothing if it changed; the drop is recorded in `public.cold_export_drops` |
| Schema | Derived from `company_id`; a registry `schema_name` that disagrees is refused |

Deferred cold-layer items and their triggers: [cold layer evolution](cold-layer-evolution.md).

## ADR-0025 dec 5

Notebook reads of the cold layer go through a tenant-scoping broker.

| Code | Role |
|---|---|
| `services/notebook_cold_broker/broker.py::build_app` | Cold-layer broker |
| `services/notebook_cold_broker/policy.py` | Resolves the handle to one tenant and scopes its keys |

## ADR-0025 dec 6

| Level | Form |
|---|---|
| Directory | `<tenant prefix>/year=YYYY/month=MM/day=DD/`, by event time |
| Rows in a part | Ordered by `equipment_id`, then `time` |

| Code | Role |
|---|---|
| `services/cold_export/naming.py::day_partition` | Day directory |
| `services/cold_export/source.py` | Reads a day `ORDER BY equipment_id, time` |
| `services/cold_export/exporter.py::_without_archived` | Re-sorts a later part after the join |

## ADR-0025 dec 10

The cold layer uses the platform's MinIO server; `minio-init` adds the cold bucket and its principals.

| Code | Role |
|---|---|
| `services/cold_export/config.py::StoreConfig` | Endpoint defaults to the platform's object store |
| `services/cold_export/store.py::S3ColdStore` | Exporter's object-store adapter |
| `infrastructure/minio/minio-init.sh` | Cold bucket and its principals; Compose runs it, Helm through the link `deploy/helm/industryflow/files/minio-init.sh` |

## ADR-0025 dec 13

The backup job mirrors the cold layer with a read-only principal.

| Code | Role |
|---|---|
| `infrastructure/backup/db-backup.sh` | Cold-layer mirror |

## ADR-0025 dec 14

| Principal | Cold-bucket verbs |
|---|---|
| `cold-export-rw` | List, put, get; no delete |
| `cold-broker-ro`, `backup-read` | List, get |
| `tenant-delete-cold` | List and delete under `tenant_*`; no get, no put |

The tenant-delete job lists `tenant_<uuid>/` and deletes in batches until the listing is empty.

| Code | Role |
|---|---|
| `services/tenant_delete/cold_store.py::ColdStore` | Purge of a tenant's cold objects |
| `services/tenant_delete/config.py::ColdConfig` | The job's cold principal |
| `infrastructure/minio/minio-init.sh` | `delete_policy` for the tenant-delete principal |
| `tests/config_guards/test_object_store_principals.py::test_only_the_tenant_delete_job_deletes_in_the_cold_bucket_and_it_sees_nothing` | Proof: no other principal deletes in the cold bucket |
| `tests/object_store/test_scoped_principals.py` | Proof: each principal is refused outside its scope on a real store |
| `services/tenant_delete/tests/test_cold_store_live.py` | Proof: purge on a real MinIO removes only the tenant's objects |

## ADR-0039 dec 1-4

One library test decides tenant ownership of a name or key ([tenant names](#tenant-names)).

| Function | Rule |
|---|---|
| `is_plain_key` | No empty, `.` or `..` segment, no backslash, no control character |
| `owns_key` | One leading `/` dropped as path syntax, a second refused as an empty segment; plain and under the prefix; with `encoded=True`, a key holding `%` is refused |
| `scope_key` | Drops one leading `/` as `owns_key` does; places a plain path under the prefix (idempotent), keeps one trailing `/`; a second trailing `/` or any other path raises `ValueError` |

| Code | Role |
|---|---|
| `libs/industryflow_contracts/industryflow_contracts/tenancy.py::owns_key` | Ownership test |
| `libs/industryflow_contracts/industryflow_contracts/tenancy.py::scope_key` | Placing a caller's path |
| `services/notebook_cold_broker/policy.py::scope_key` | Cold broker binds it to the caller's tenant |
| `services/notebook_tracking_gateway/policy.py::owns_artifact_key` | Tracking gateway tests an encoded artifact key |
| `libs/industryflow_contracts/tests/test_tenancy.py::test_what_scope_key_places_owns_key_accepts` | Proof: placing and testing agree |
| `libs/industryflow_contracts/tests/test_tenancy.py::test_only_one_leading_separator_is_path_syntax` | Proof: a second leading `/` is refused |
| `tests/config_guards/test_tenant_contract_copies.py` | No copy of the test |

## ADR-0039 dec 5

Every mediator and read path uses that test.

| Code | Role |
|---|---|
| `services/notebook_tracking_gateway/policy.py::owns_artifact_key` | Tracking gateway |
| `services/notebook_cold_broker/policy.py::owns_key` | Cold-layer broker |
| `services/ml_service/api/mlflow_namespace.py::strip_owned` | ML service read paths |

Links: [schema scripts](../../infrastructure/timescaledb/init-scripts/README.md) (scripts, roles and grants), [ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) (idempotent writes against `UNIQUE` keys), [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) (tenant provisioning), [ADR-0034](../../ADR/ADR-0034-platform-schema-migrations.md) (applying init-script changes to a live database), [cold-layer deferred items](cold-layer-evolution.md).
