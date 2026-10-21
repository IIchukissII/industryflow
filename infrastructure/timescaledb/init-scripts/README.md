<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# TimescaleDB init scripts

Initialisation of the `industryflow` and `mlflow` databases: scripts, roles and manual operations.
The tables they create: [data and storage](../../../docs/architecture/data-and-storage.md#tables).
Applying changes to a live database: [ADR-0034](../../../ADR/ADR-0034-platform-schema-migration-strategy.md).

## Execution

| Deployment | Mount | Runs |
|---|---|---|
| Compose | `/docker-entrypoint-initdb.d` | In filename order, on an empty data volume only |
| Helm | `/docker-entrypoint-initdb.d` via the `<release>-db-init` ConfigMap (`files/db-init` symlink) | Same |

On an existing database a new or changed script is applied by hand ([manual operations](#manual-operations)).

## Scripts

| Script | Creates or changes | Decision |
|---|---|---|
| `00-create-databases.sql` | Databases `industryflow`, `mlflow`; TimescaleDB extension | |
| `00-create-roles.sh` | Service roles and connection limits ([roles](#roles)) | [ADR-0017](../../../ADR/ADR-0017-database-tls-and-authentication.md) |
| `01-init-schema.sql` | `public.companies` (tenant registry), `update_updated_at()` | [ADR-0003](../../../ADR/ADR-0003-tenant-to-schema-resolution.md) |
| `02-tenant-alert-tables.sql` | `add_alert_tables_to_schema()`: `alert_rules`, `alerts` | |
| `03-tenant-ml-tables.sql` | `add_ml_tables_to_schema()`: `ml_models`, `model_predictions`, view `labeled_predictions` | |
| `04-complete-tenant-creation.sql` | `create_tenant_schema(p_company_id UUID, p_company_name VARCHAR)`, `create_base_tables()`, `create_aggregation_tables()`, role grants, `tenant_reader_<uuid>` | [ADR-0003](../../../ADR/ADR-0003-tenant-to-schema-resolution.md) |
| `05-grant-mlflow-permissions.sql` | `mlflow_user` privileges on the `mlflow` database | |
| `06-feature-engineering-tables.sql` | `feature_engineering_configs`; `ml_models.feature_config_id`, `equipment_type`; backfill | |
| `08-ml-alert-permissions-migration.sql` | `alert_service_user` `SELECT` on `feature_engineering_configs`, `ml_models`; backfill | |
| `09-tenant-reader-roles-migration.sql` | `ensure_tenant_reader_role()`; backfill of `tenant_reader_<uuid>` | [ADR-0012](../../../ADR/ADR-0012-notebook-credential-delivery.md) |
| `10-spark-aggregation-update-grant.sql` | `spark_streaming_user` `UPDATE` on tenant tables; backfill | [ADR-0006](../../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) |
| `11-alert-connection-limit.sql` | `alert_service_user` `CONNECTION LIMIT 50` | |
| `12-metrics-role.sql` | `metrics_user` (`pg_monitor`) | [ADR-0016](../../../ADR/ADR-0016-observability-and-monitoring-integration.md) |
| `13-notebook-sql-proxy-role.sh` | `tenant_sqlproxy_<uuid>` logins, `notebook_sql_tenants`, `notebook_sql_proxy`, `ensure_tenant_sql_login()`, `notebook_sql_proxy_enrol()`, `tenant_is_suspended()` | [ADR-0015](../../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) |
| `14-model-drift-reference-profile.sql` | `ml_models.reference_profile` | [ADR-0021](../../../ADR/ADR-0021-model-drift-monitoring.md) |
| `15-alert-labels.sql` | `alert_labels` | [ADR-0022](../../../ADR/ADR-0022-concept-drift-operator-feedback.md) |
| `16-cold-layer-raw-retention.sql` | `public.cold_export_drops`, `cold_export_day_fingerprint()`, `cold_export_drop_day()`, `ensure_cold_export_access()`; removes raw retention timers | [ADR-0025](../../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) |
| `17-stateful-feature-kill-switch.sql` | `public.platform_config`, row `stateful_features_enabled` | [ADR-0024](../../../ADR/ADR-0024-stateful-feature-kill-switch.md) |
| `18-model-compatibility-status.sql` | `ml_models.compatibility_status`, `compatibility_detail`, `compatibility_checked_at` | [ADR-0027](../../../ADR/ADR-0027-model-artifact-supply-chain-parity.md) |
| `19-model-provenance.sql` | `ml_models.provenance`, `artifact_uri` | [ADR-0030](../../../ADR/ADR-0030-externally-authored-model-admission.md) |
| `20-model-detector.sql` | `ml_models.detector` | [ADR-0028](../../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) |
| `21-company-provisioning.sql` | `provision_company()`, `update_company()`, `end_suspended_tenant_sql_sessions()`, `company_provisioning_guard()` | [ADR-0033](../../../ADR/ADR-0033-tenant-lifecycle.md) |
| `22-sensor-display-name.sql` | `add_display_name_column_to_sensors()`: `sensors.display_name`; backfill over existing tenants | [ADR-0034](../../../ADR/ADR-0034-platform-schema-migration-strategy.md) |
| `23-sensor-latest-reading-index.sql` | `add_latest_reading_index_to_sensor_measurements()`: index `sensor_measurements (sensor_id, time DESC)`; backfill over existing tenants | [ADR-0034](../../../ADR/ADR-0034-platform-schema-migration-strategy.md) |
| `24-writers-read-tenant-status.sql` | `spark_streaming_user`, `alert_service_user` `SELECT (schema_name, is_active)` on `public.companies` | [ADR-0033](../../../ADR/ADR-0033-tenant-lifecycle.md) |

`create_tenant_schema()` calls the per-schema functions of scripts 02, 03, 06, 13–15, 18–20, 22 and 23.
Scripts 06 and later are idempotent; the per-tenant ones also apply themselves to every existing
tenant schema.

## Roles

| Role | Used by | Access | Connection limit |
|---|---|---|---|
| `api_gateway_user` | `api-gateway` | `SELECT/INSERT/UPDATE/DELETE` on tenant tables; `SELECT` on `public.companies`; `EXECUTE` on `provision_company()`, `update_company()`, `end_suspended_tenant_sql_sessions()` | 50 |
| `spark_streaming_user` | `spark-streaming`, `spark-aggregations` | `SELECT/INSERT/UPDATE` on tenant tables; `SELECT (schema_name, is_active)` on `public.companies` | 50 |
| `alert_service_user` | `alert-service-detector` | `SELECT/INSERT/UPDATE/DELETE` on tenant tables; `SELECT (schema_name, is_active)` on `public.companies` | 50 |
| `ml_service_user` | `ml-service-api` | `SELECT/INSERT/UPDATE/DELETE` on tenant tables; `SELECT` on `public.companies`, `public.platform_config` | 10 |
| `mlflow_user` | `mlflow` | `mlflow` database | 20 |
| `metrics_user` | `postgres-exporter` | `pg_monitor` | 5 |
| `cold_export_user` | `cold-export` | `SELECT` on `sensor_measurements`, `public.companies`; `EXECUTE` on `cold_export_day_fingerprint()`, `cold_export_drop_day()` | 5 |
| `tenant_reader_<uuid>` | SQL proxy, via `SET ROLE` | `NOLOGIN`; `SELECT` on its own tenant schema | |
| `tenant_sqlproxy_<uuid>` | SQL proxy, one login per tenant | `LOGIN NOINHERIT`; member of its `tenant_reader_<uuid>` and `notebook_sql_tenants` | 20 |
| `notebook_sql_proxy` | SQL proxy enrolment | `EXECUTE` on `notebook_sql_proxy_enrol()`; member of nothing | 5 |

Ingestion has no database role. `tests/config_guards/test_db_connection_budget.py` checks that
`max_connections` covers the sum of the limits.

| Password variable | Role |
|---|---|
| `API_GATEWAY_DB_PASSWORD` | `api_gateway_user` |
| `SPARK_STREAMING_DB_PASSWORD` | `spark_streaming_user` |
| `ALERT_SERVICE_DB_PASSWORD` | `alert_service_user` |
| `ML_SERVICE_DB_PASSWORD` | `ml_service_user` |
| `MLFLOW_DB_PASSWORD` | `mlflow_user` |
| `METRICS_DB_PASSWORD` | `metrics_user` |
| `COLD_EXPORT_DB_PASSWORD` | `cold_export_user` (may be unset; role still created) |
| `NOTEBOOK_SQL_PROXY_DB_PASSWORD` | `notebook_sql_proxy` (role created only when set) |

## Manual operations

| Task | Command |
|---|---|
| Apply a SQL script to an existing database | `psql -v ON_ERROR_STOP=1 -U postgres -d industryflow -f <script>.sql` |
| Deactivate a tenant in SQL | `SELECT update_company('<company-id>', NULL, false);` then, in a new transaction, `SELECT end_suspended_tenant_sql_sessions('<company-id>');` |
| List tenant schemas | `SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%';` |
| Check a role's grants in a tenant | `SELECT table_name, privilege_type FROM information_schema.table_privileges WHERE table_schema = 'tenant_<uuid>' AND grantee = '<role>';` |

Provisioning and deactivating tenants through the API or script:
[user management](../../../docs/operations/user-management.md#tenants).

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `permission denied for table …` in a tenant created before a grant script | Grant backfill not applied | Re-apply the script that adds the grant (08, 10, 15, 16) |
| `column "…" does not exist` on `ml_models` | Column migration not applied to that schema | Re-apply 06, 14, 18, 19 or 20, or `SELECT add_<…>_to_ml_models('tenant_<uuid>');` |
| `column s.display_name does not exist` | 22 not applied to that schema | Re-apply 22, or `SELECT add_display_name_column_to_sensors('tenant_<uuid>');` |
| Changed init script has no effect | Init scripts run on an empty data volume only | Apply the script by hand |
