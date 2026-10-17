<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Data and storage

Where tenant data lives: PostgreSQL + TimescaleDB with one schema per tenant, and the cold layer in
the object store. Scripts, roles and grants: [init scripts](../../infrastructure/timescaledb/init-scripts/README.md).

| Decision | ADR |
|---|---|
| Schema-per-tenant; tenant resolved from a verified identity, applied with `SET LOCAL search_path` in a transaction | [ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md) |
| Idempotent writes against `UNIQUE` keys | [ADR-0006](../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) |
| Cold layer, export → verify → drop, raw retention owned by the exporter | [ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) |
| Tenant provisioning | [ADR-0033](../../ADR/ADR-0033-tenant-lifecycle.md) |
| Applying init-script changes to a live database | [ADR-0034](../../ADR/ADR-0034-platform-schema-migration-strategy.md) |

## Tenant identity sources

| Caller | `company_id` taken from |
|---|---|
| API request | `company_id` JWT claim |
| Device reading | Client-certificate SAN, stamped by ingestion ([ADR-0002](../../ADR/ADR-0002-ingestion-authentication-and-device-identity.md)) |
| Spark, alert worker | The Kafka message (stamped by ingestion) |
| Notebook kernel | The capability handle ([notebooks](notebooks.md)) |

Provisioning: [user management](../operations/user-management.md#tenants).

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

| Table | Content |
|---|---|
| `companies` | `company_id` → `schema_name` registry |
| `user` | Platform users (fastapi-users, `services/api_gateway/models/user.py`) |
| `platform_config` | Platform switches (`stateful_features_enabled`, [ADR-0024](../../ADR/ADR-0024-stateful-feature-kill-switch.md)) |
| `cold_export_drops` | Committed cold-export drops |

Per tenant schema, created by `create_tenant_schema()`:

| Table | Hypertable on, chunk | Key | Compression (segment by, after) | Retention |
|---|---|---|---|---|
| `equipment` | | | | |
| `sensors` | | `UNIQUE (equipment_id, sensor_name)` | | |
| `sensor_measurements` | `time`, 1 d | `UNIQUE (time, sensor_id)` | `equipment_id`, 7 d | None; the cold export drops it |
| `sensor_aggregations_1min` | `time`, 7 d | `UNIQUE (time, sensor_id, equipment_id)` | `equipment_id`, 14 d | 90 d |
| `sensor_aggregations_5min` | `time`, 30 d | same | `equipment_id`, 30 d | 180 d |
| `sensor_aggregations_1hour` | `time`, 90 d | same | `equipment_id`, 60 d | 1 y |
| `alert_rules` | | | | |
| `alerts` | `triggered_at`, 7 d | | | 90 d |
| `alert_labels` | | | | |
| `ml_models` | | `UNIQUE (model_name, model_version)` | | |
| `model_predictions` | `timestamp`, 7 d | | `model_id`, 30 d | 180 d |
| `feature_engineering_configs` | | | | |

Extension-owned domain tables: [extensions](../operations/extensions.md).

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
| Read path | `services/notebook_cold_broker/`, client `industryflow.IndustryFlowCold` ([notebooks](notebooks.md)) |
| Backup | `db-backup` mirrors the cold bucket (`COLD_SOURCE_*`, Helm `backup.cold.enabled`) |

Exporter behaviour per tenant and day:

| Step | Detail |
|---|---|
| Run lock | Session advisory lock for the whole run; a run that finds it held exits 0 |
| Export | Day read from one snapshot, written as a part: `measurements.parquet`, then `measurements-0001.parquet`, … for rows that arrive later |
| Verify | Written row count reconciled against the source; part listed in the day's `_manifest.json` |
| Drop | `cold_export_drop_day` re-checks the day's fingerprint (count + digest) under a lock and drops nothing if it changed; the drop is recorded in `public.cold_export_drops` |
| Schema | Derived from `company_id`; a registry `schema_name` that disagrees is refused |

Deferred cold-layer items and their triggers: [cold layer evolution](cold-layer-evolution.md).
