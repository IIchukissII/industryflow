<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Data and storage

Where tenant data lives: PostgreSQL + TimescaleDB with one schema per tenant, and the cold layer in
the object store. The SQL in `infrastructure/timescaledb/init-scripts/` is authoritative for the
schema.

| Decision | ADR |
|---|---|
| Schema-per-tenant; tenant resolved from a verified identity, applied with `SET LOCAL search_path` in a transaction | [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md) |
| Idempotent writes against `UNIQUE` keys | [ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) |
| Cold layer, export → verify → drop, raw retention owned by the exporter | [ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) |
| Tenant provisioning | [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) (proposed) |
| Applying init-script changes to a live database | [ADR-0034](../../ADR/ADR-0034-platform-schema-migration-strategy.md) (proposed) |

## Tenant identity sources

| Caller | `company_id` taken from |
|---|---|
| API request | `company_id` JWT claim |
| Device reading | Client-certificate SAN, stamped by ingestion ([ADR-0002](../../ADR/ADR-0002-ingestion-authentication-and-device-identity.md)) |
| Spark, alert worker | The Kafka message (stamped by ingestion) |
| Notebook kernel | The capability handle ([notebooks.md](notebooks.md)) |

Provisioning: `scripts/create-tenant.sh "<Company Name>" [<company-id>]` inserts the
`public.companies` row and calls `create_tenant_schema()`.

## Tenant names

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
| `tests/config_guards/test_tenant_contract_copies.py` | A service or script spelling a tenant name or restating the window contract in code |
| `tests/config_guards/test_images_carry_contracts.py` | An image whose code imports the package but does not install it |

## Tables

`public` schema:

| Table | Content | Created by |
|---|---|---|
| `companies` | `company_id` → `schema_name` registry | `01-init-schema.sql` |
| `user` | Platform users (fastapi-users) | `services/api_gateway/models/user.py` |
| `platform_config` | Platform switches (`stateful_features_enabled`, [ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)) | `17-stateful-feature-kill-switch.sql` |
| `cold_export_drops` | Committed cold-export drops | `16-cold-layer-raw-retention.sql` |

Per tenant schema (`create_tenant_schema()` in `04-complete-tenant-creation.sql`):

| Table | Kind | Key / retention | Defined in |
|---|---|---|---|
| `equipment`, `sensors` | Plain | `UNIQUE (equipment_id, sensor_name)` on `sensors` | `04-complete-tenant-creation.sql` |
| `sensor_measurements` | Hypertable on `time` | `UNIQUE (time, sensor_id)`; no retention policy (cold export drops it) | `04-complete-tenant-creation.sql`, `16-cold-layer-raw-retention.sql` |
| `sensor_aggregations_1min` / `_5min` / `_1hour` | Hypertable on `time` | `UNIQUE (time, sensor_id, equipment_id)`; retention 90 d / 180 d / 1 y | `04-complete-tenant-creation.sql` |
| `alert_rules` | Plain | | `02-tenant-alert-tables.sql` |
| `alerts` | Hypertable on `triggered_at` | Retention 90 d | `02-tenant-alert-tables.sql` |
| `alert_labels` | Plain | No retention | `15-alert-labels.sql` |
| `ml_models` | Plain | | `03-tenant-ml-tables.sql`; columns added by `14`, `18`–`20` |
| `model_predictions` | Hypertable on `timestamp` | Retention 180 d | `03-tenant-ml-tables.sql` |
| `feature_engineering_configs` | Plain | | `06-feature-engineering-tables.sql` |

Compression (`segmentby equipment_id`, `orderby time DESC`) after: raw 7 d; aggregations
14 d / 30 d / 60 d. Extension-owned domain tables: [operations/extensions.md](../operations/extensions.md).

Init scripts run only on an empty data volume. On a live database a new or changed script is applied
by hand.

## Roles

Created in `00-create-roles.sh` (and `12-metrics-role.sql`, `13-notebook-sql-proxy-role.sh`).
Per-tenant grants are made by `create_tenant_schema()`.

| Role | Used by | Tenant-schema access | Connection limit |
|---|---|---|---|
| `api_gateway_user` | api-gateway | SELECT, INSERT, UPDATE, DELETE | 50 |
| `spark_streaming_user` | spark-streaming, spark-aggregations | SELECT, INSERT, UPDATE | 50 |
| `alert_service_user` | alert-service-detector | SELECT, INSERT, UPDATE, DELETE | 50 |
| `ml_service_user` | ml-service-api | SELECT, INSERT, UPDATE, DELETE | 10 |
| `cold_export_user` | cold-export | SELECT on `sensor_measurements`; EXECUTE on `cold_export_day_fingerprint`, `cold_export_drop_day` | 5 |
| `mlflow_user` | mlflow (own `mlflow` database) | none | 20 |
| `metrics_user` | postgres-exporter (`pg_monitor`) | none | 5 |
| `tenant_reader_<uuid>` | Assumed by the SQL proxy via `SET ROLE` (`NOLOGIN`) | SELECT on current and future tables of its own schema | |
| `tenant_sqlproxy_<uuid>` | SQL proxy login per tenant (`NOINHERIT`) | Member of its own reader role only | |
| `notebook_sql_proxy` | SQL proxy enrolment | none | 5 |

Ingestion has no database role ([ADR-0002](../../ADR/ADR-0002-ingestion-authentication-and-device-identity.md)).
`tests/config_guards/test_db_connection_budget.py` checks that `max_connections` covers the sum of these limits.

## Cold layer

Raw `sensor_measurements` older than the export horizon, as Parquet in the object store.

| Component | Where |
|---|---|
| Exporter (single-node PyArrow) | `services/cold_export/` — `exporter.py` (ordering), `source.py` (database reads, run lock), `store.py` (object store), `naming.py` (paths), `ports.py` |
| Schedule | Helm `cronjob-cold-export.yaml` (`coldExport.schedule`, `concurrencyPolicy: Forbid`); compose `docker compose --profile cold-export up cold-export` |
| Horizon | `COLD_EXPORT_HORIZON_DAYS` / `coldExport.horizonDays`, default 30 |
| Bucket | `COLD_STORE_BUCKET` / `coldExport.store.bucket`, default `industryflow-cold` |
| Database side | `16-cold-layer-raw-retention.sql`: `cold_export_day_fingerprint`, `cold_export_drop_day` (`SECURITY DEFINER`), `public.cold_export_drops` |
| Object-store principals | Write-scoped exporter principal; read-only broker principal; created by `minio-init` / Helm `job-minio-init.yaml` |
| Read path | `services/notebook_cold_broker/`, client `industryflow.IndustryFlowCold` ([notebooks.md](notebooks.md)) |
| Backup | `db-backup` mirrors the cold bucket (`COLD_SOURCE_*`, Helm `backup.cold.enabled`) |

Exporter behaviour per tenant and day:

| Step | Detail |
|---|---|
| Run lock | Session advisory lock for the whole run; a run that finds it held exits 0 |
| Export | Day read from one snapshot, written as a part: `measurements.parquet`, then `measurements-0001.parquet`, … for rows that arrive later |
| Verify | Written row count reconciled against the source; part listed in the day's `_manifest.json` |
| Drop | `cold_export_drop_day` re-checks the day's fingerprint (count + digest) under a lock and drops nothing if it changed; the drop is recorded in `public.cold_export_drops` |
| Schema | Derived from `company_id`; a registry `schema_name` that disagrees is refused |

Deferred cold-layer items and their triggers: [cold-layer-evolution.md](cold-layer-evolution.md).
