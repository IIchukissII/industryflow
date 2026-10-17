<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# TimescaleDB init scripts

Database initialization for the `industryflow` and `mlflow` databases: scripts, roles, tenant schema layout and manual operations. The layout is schema-per-tenant ([ADR-0003](../../../ADR/ADR-0003-tenant-to-schema-resolution.md)); the script sequence follows [ADR-0034](../../../ADR/ADR-0034-platform-schema-migration-strategy.md).

## Execution

| Path | Mount | Runs |
|---|---|---|
| Compose | `/docker-entrypoint-initdb.d` | In filename order, on an empty data volume only |
| Helm | `/docker-entrypoint-initdb.d` via the `<release>-db-init` ConfigMap (`files/db-init` symlink) | Same; see the [chart README](../../../deploy/helm/industryflow/README.md) |

## Scripts

| Script | Creates or changes | Decision |
|---|---|---|
| `00-create-databases.sql` | Databases `industryflow`, `mlflow`; TimescaleDB extension | |
| `00-create-roles.sh` | Service roles and connection limits (see [Roles](#roles)) | [ADR-0017](../../../ADR/ADR-0017-database-tls-and-authentication.md) |
| `01-init-schema.sql` | `public.companies` (tenant registry), `update_updated_at()` | ADR-0003 |
| `02-tenant-alert-tables.sql` | `add_alert_tables_to_schema()`: `alert_rules`, `alerts` | |
| `03-tenant-ml-tables.sql` | `add_ml_tables_to_schema()`: `ml_models`, `model_predictions`, view `labeled_predictions` | |
| `04-complete-tenant-creation.sql` | `create_tenant_schema(p_company_id UUID, p_company_name VARCHAR)`, `create_base_tables()`, `create_aggregation_tables()`, role grants, `tenant_reader_<uuid>` | ADR-0003 |
| `05-grant-mlflow-permissions.sql` | `mlflow_user` privileges on the `mlflow` database | |
| `06-feature-engineering-tables.sql` | `add_feature_engineering_tables_to_schema()`: `feature_engineering_configs`; `add_feature_config_columns_to_ml_models()`; backfill | |
| `08-ml-alert-permissions-migration.sql` | `grant_ml_alert_permissions()`: `alert_service_user` `SELECT` on `feature_engineering_configs`, `ml_models`; backfill | |
| `09-tenant-reader-roles-migration.sql` | `ensure_tenant_reader_role()`; backfill of `tenant_reader_<uuid>` | [ADR-0011](../../../ADR/ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md), [ADR-0012](../../../ADR/ADR-0012-notebook-credential-delivery.md) |
| `10-spark-aggregation-update-grant.sql` | `spark_streaming_user` `UPDATE` on tenant tables; backfill | [ADR-0006](../../../ADR/ADR-0006-spark-windowing-and-idempotent-writes.md) |
| `11-alert-connection-limit.sql` | `alert_service_user` `CONNECTION LIMIT 50` | |
| `12-metrics-role.sql` | `metrics_user` (`pg_monitor`, limit 5) | [ADR-0016](../../../ADR/ADR-0016-observability-and-monitoring-integration.md) |
| `13-notebook-sql-proxy-role.sh` | `tenant_sqlproxy_<uuid>` logins, `notebook_sql_tenants`, `notebook_sql_proxy`, `ensure_tenant_sql_login()`, `notebook_sql_proxy_enrol()`, `tenant_is_suspended()` | [ADR-0015](../../../ADR/ADR-0015-notebook-capability-minting-and-sql-proxy.md) |
| `14-model-drift-reference-profile.sql` | `ml_models.reference_profile` | [ADR-0021](../../../ADR/ADR-0021-model-drift-monitoring.md) |
| `15-alert-labels.sql` | `add_alert_labels_to_schema()`: `alert_labels` | [ADR-0022](../../../ADR/ADR-0022-concept-drift-operator-feedback.md) |
| `16-cold-layer-raw-retention.sql` | `public.cold_export_drops`, `cold_export_day_fingerprint()`, `cold_export_drop_day()`, `ensure_cold_export_access()`; removes raw retention timers | [ADR-0025](../../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) |
| `17-stateful-feature-kill-switch.sql` | `public.platform_config`, row `stateful_features_enabled` | [ADR-0024](../../../ADR/ADR-0024-stateful-feature-kill-switch.md) |
| `18-model-compatibility-status.sql` | `add_compatibility_columns_to_ml_models()` | [ADR-0027](../../../ADR/ADR-0027-model-artifact-supply-chain-parity.md) |
| `19-model-provenance.sql` | `add_provenance_columns_to_ml_models()` | [ADR-0030](../../../ADR/ADR-0030-externally-authored-model-admission.md) |
| `20-model-detector.sql` | `add_detector_column_to_ml_models()` | [ADR-0028](../../../ADR/ADR-0028-model-adapter-contract-and-score-semantics.md) |
| `21-company-provisioning.sql` | `provision_company()`, `update_company()`, `end_suspended_tenant_sql_sessions()`, `company_provisioning_guard()` | [ADR-0033](../../../ADR/ADR-0033-tenant-lifecycle.md) |

`create_tenant_schema()` calls the per-schema functions of scripts 02, 03, 06, 13–15 and 18–20 at
call time. Scripts 06 and later are idempotent; the per-tenant ones also apply themselves to every
existing tenant schema.

## Roles

| Role | Created by | Access | Connection limit |
|---|---|---|---|
| `api_gateway_user` | 00 | `SELECT/INSERT/UPDATE/DELETE` on tenant tables; `SELECT` on `public.companies`; `EXECUTE` on `provision_company()`, `update_company()`, `end_suspended_tenant_sql_sessions()` | 50 |
| `spark_streaming_user` | 00 | `SELECT/INSERT/UPDATE` on tenant tables | 50 |
| `alert_service_user` | 00 | `SELECT/INSERT/UPDATE/DELETE` on tenant tables | 50 |
| `ml_service_user` | 00 | `SELECT/INSERT/UPDATE/DELETE` on tenant tables; `SELECT` on `public.companies`, `public.platform_config` | 10 |
| `mlflow_user` | 00 | `mlflow` database | 20 |
| `metrics_user` | 00, 12 | `pg_monitor` | 5 |
| `cold_export_user` | 00 | `SELECT` on `sensor_measurements`, `public.companies`; `EXECUTE` on `cold_export_drop_day()` | 5 |
| `tenant_reader_<uuid>` | 04, 09 | `NOLOGIN`; `SELECT` on its own tenant schema | |
| `tenant_sqlproxy_<uuid>` | 13 | `LOGIN NOINHERIT`; member of its `tenant_reader_<uuid>` and `notebook_sql_tenants` | |
| `notebook_sql_proxy` | 13 | `EXECUTE` on `notebook_sql_proxy_enrol()`; member of nothing | |

Passwords come from the environment at init:

| Env var | Role |
|---|---|
| `API_GATEWAY_DB_PASSWORD` | `api_gateway_user` |
| `SPARK_STREAMING_DB_PASSWORD` | `spark_streaming_user` |
| `ALERT_SERVICE_DB_PASSWORD` | `alert_service_user` |
| `ML_SERVICE_DB_PASSWORD` | `ml_service_user` |
| `MLFLOW_DB_PASSWORD` | `mlflow_user` |
| `METRICS_DB_PASSWORD` | `metrics_user` |
| `COLD_EXPORT_DB_PASSWORD` | `cold_export_user` (may be unset; role still created) |
| `NOTEBOOK_SQL_PROXY_DB_PASSWORD` | `notebook_sql_proxy` (role created only when set) |

## Tenant schema

Schema name: `tenant_` + the company UUID with `-` replaced by `_`
(`tenant_<uuid-with-underscores>`).

| Table | Hypertable chunk | Compression (segment by, after) | Retention |
|---|---|---|---|
| `equipment` | | | |
| `sensors` | | | |
| `sensor_measurements` | 1 day | `equipment_id`, 7 days | Cold-export job ([ADR-0025](../../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md)); no timer |
| `sensor_aggregations_1min` | 7 days | `equipment_id`, 14 days | 90 days |
| `sensor_aggregations_5min` | 30 days | `equipment_id`, 30 days | 180 days |
| `sensor_aggregations_1hour` | 90 days | `equipment_id`, 60 days | 1 year |
| `alert_rules` | | | |
| `alerts` | 7 days | | 90 days |
| `alert_labels` | | | |
| `ml_models` | | | |
| `model_predictions` | 7 days | `model_id`, 30 days | 180 days |
| `feature_engineering_configs` | | | |

`public` holds `companies`, `platform_config` and `cold_export_drops`.

## Manual operations

| Task | Command |
|---|---|
| Provision a tenant | `scripts/create-tenant.sh "<Company Name>" [<company-uuid>]` |
| Provision via the API | `POST /api/companies` (superuser; calls `provision_company()`) |
| Deactivate a tenant | `PUT /api/companies/<company-id>`, or `SELECT update_company('<company-id>', NULL, false);` then, in a new transaction, `SELECT end_suspended_tenant_sql_sessions('<company-id>');` |
| Apply a SQL script to an existing database | `psql -v ON_ERROR_STOP=1 -U postgres -d industryflow -f <script>.sql` |
| List tenant schemas | `SELECT schema_name FROM information_schema.schemata WHERE schema_name LIKE 'tenant_%';` |
| Check a role's grants in a tenant | `SELECT table_name, privilege_type FROM information_schema.table_privileges WHERE table_schema = 'tenant_<uuid>' AND grantee = '<role>';` |

No tenant is seeded; `public.companies` starts empty. Use the printed `company_id` for
`ADMIN_USER_*_COMPANY_ID` before `scripts/seed_users.py`.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `permission denied for table …` in a tenant created before a grant script | Grant backfill not applied | Re-apply the script that adds the grant (08, 10, 15, 16) |
| `column "…" does not exist` on `ml_models` | Column migration not applied to that schema | Re-apply 06, 14, 18, 19 or 20, or `SELECT add_<…>_to_ml_models('tenant_<uuid>');` |
| Changed init script has no effect on a running database | Init scripts run on an empty data volume only | Apply the script by hand (see above) |

## Related

| Doc | Topic |
|---|---|
| [data-and-storage.md](../../../docs/architecture/data-and-storage.md) | Data layout |
| [ml-and-features.md](../../../docs/architecture/ml-and-features.md) | Feature configs, models |
| [alerting.md](../../../docs/architecture/alerting.md) | Alert rules, evaluation |
| [GLOSSARY.md](../../../ADR/GLOSSARY.md) | Schema-per-tenant, Per-tenant read-only role |
