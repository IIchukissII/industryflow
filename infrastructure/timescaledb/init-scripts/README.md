<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors

SPDX-License-Identifier: CC-BY-SA-4.0
-->

# TimescaleDB Initialization Scripts

**Architecture:** Schema-per-Tenant (v5.0)
**Purpose:** Automated database initialization for IndustryFlow

---

## Execution Order

Scripts are executed in alphanumeric order by Docker container initialization:

### 00 - Foundation Setup

**`00-create-databases.sql`**
- Creates `industryflow` (main) and `mlflow` (MLflow tracking) databases
- Enables TimescaleDB extension

**`00-create-roles.sh`**
- Creates application database roles:
  - `api_gateway_user` - Full CRUD access to tenant data
  - `spark_streaming_user` - SELECT and INSERT for aggregations
  - `alert_service_user` - CRUD for alerts, SELECT for feature configs
  - `ml_service_user` - CRUD for ML models and feature configs
  - `mlflow_user` - Full access to MLflow database

### 01 - Public Schema

**`01-init-schema.sql`**
- Creates `public.companies` table (tenant registry)
- Schema-per-tenant isolation architecture

### 02-03 - Tenant Table Definitions

**`02-tenant-alert-tables.sql`**
- `add_alert_tables_to_schema()` function
- Creates:
  - `alert_rules` - Alert rule configurations
  - `alerts` - Triggered alerts (hypertable with compression)

**`03-tenant-ml-tables.sql`**
- `add_ml_tables_to_schema()` function
- Creates:
  - `ml_models` - ML model registry
  - `ml_predictions` - Model prediction history (hypertable)

### 04 - Complete Tenant Creation

**`04-complete-tenant-creation.sql`**
- **Main function:** `create_tenant_schema(company_id UUID, company_name VARCHAR)`
- Creates complete isolated tenant schema with:
  - Equipment and sensors tables
  - Sensor measurements (hypertable, compressed)
  - 3 aggregation tables (1min, 5min, 1hour)
  - Alert tables
  - ML tables
  - Feature engineering tables
- Grants permissions to all application roles
- Callable from API for dynamic tenant provisioning

### 05 - MLflow Permissions

**`05-grant-mlflow-permissions.sql`**
- Grants `mlflow_user` permissions on MLflow database

### 06 - Feature Engineering (ML Enhancement)

**`06-feature-engineering-tables.sql`**
- `add_feature_engineering_tables_to_schema()` function
- Creates `feature_engineering_configs` table:
  - `base_sensors` JSONB - List of required sensor names
  - `transformations` JSONB - Feature transformation specifications
- `add_feature_config_columns_to_ml_models()` function
- Adds `feature_config_id` foreign key to `ml_models`
- **Must run before any tenant is provisioned** — `create_tenant_schema()` calls these functions.

### Provisioning tenants (runtime)

No tenants are seeded — the database initializes with an empty `public.companies`
registry and zero tenant schemas. Tenants are created at runtime, so no company is
hardcoded in the repo:

```sh
scripts/create-tenant.sh "Your Company"        # prints the new company_id + schema
```

This inserts the company and calls `create_tenant_schema()`. Use the printed
`company_id` for `ADMIN_USER_*_COMPANY_ID` (then run `scripts/seed_users.py`) or when
creating users via the API.

### 08 - ML Alert Integration (Permissions)

**`08-ml-alert-permissions-migration.sql`**
- Grants `alert_service_user` SELECT permissions on:
  - `feature_engineering_configs` - To read feature requirements
  - `ml_models` - To read model metadata
- Required for equipment-level ML alert evaluation
- **Auto-applies to all existing tenant schemas**

---

## Schema Structure

### Public Schema (Shared)

```
public
├── companies (company_id, company_name, schema_name)
└── [MLflow tables in mlflow database]
```

### Tenant Schema (Isolated per Company)

```
tenant_<normalized_uuid>
├── equipment
├── sensors
├── sensor_measurements (hypertable, compressed)
├── sensor_aggregations_1min (hypertable, compressed)
├── sensor_aggregations_5min (hypertable, compressed)
├── sensor_aggregations_1hour (hypertable, compressed)
├── alert_rules
├── alerts (hypertable, compressed)
├── ml_models
├── ml_predictions (hypertable)
└── feature_engineering_configs
```

---

## Key Features

### TimescaleDB Optimizations

**Hypertables:**
- `sensor_measurements` - Chunked by time (1 day intervals)
- `alerts` - Chunked by time (7 day intervals)
- `ml_predictions` - Chunked by time (30 day intervals)
- `sensor_aggregations_*` - Chunked by appropriate intervals

**Compression:**
- Automatic compression after retention period
- Segment by: `equipment_id`
- Order by: `time DESC`
- Compression ratios: 10-50x depending on data

**Retention Policies:**
- Raw measurements: 2 years
- 1-min aggregations: 90 days
- 5-min aggregations: 180 days
- 1-hour aggregations: 1 year
- Alerts: No automatic deletion

### Multi-Tenant Isolation

**Schema-per-Tenant Benefits:**
- Complete data isolation (no RLS complexity)
- Compatible with TimescaleDB compression
- No `company_id` filtering in queries
- Simple schema routing via `SET search_path`
- Independent schema backups/restores
- Clear tenant onboarding/offboarding

**UUID Normalization:**
```
Input:  550e8400-e29b-41d4-a716-446655440000
Output: tenant_550e8400_e29b_41d4_a716_446655440000
```

### Application Roles

| Role | Purpose | Permissions |
|------|---------|-------------|
| `api_gateway_user` | Frontend API | Full CRUD on all tables |
| `spark_streaming_user` | Stream processing | SELECT + INSERT on aggregations |
| `alert_service_user` | Alert detection | CRUD on alerts, SELECT on configs |
| `ml_service_user` | ML operations | CRUD on ML tables |
| `mlflow_user` | MLflow tracking | Full access to mlflow DB |

---

## ML Alert Detection System

### Feature Engineering Configuration

**Purpose:** Define how raw sensor data is transformed into ML features

**Structure:**
```json
{
  "id": "uuid",
  "name": "TEP Equipment Config",
  "equipment_type": "chemical_reactor",
  "base_sensors": ["xmeas_1", "xmeas_2", ..., "xmv_11"],
  "transformations": [
    {
      "name": "xmeas_1_identity",
      "type": "identity",
      "sensor": "xmeas_1"
    },
    {
      "name": "xmeas_1_squared",
      "type": "polynomial",
      "sensor": "xmeas_1",
      "params": {"power": 2}
    },
    {
      "name": "pressure_ratio",
      "type": "interaction",
      "sensors": ["xmeas_7", "xmeas_13"],
      "params": {"operation": "ratio"}
    },
    {
      "name": "flow_deviation_from_mean",
      "type": "statistical",
      "sensor": "xmeas_1",
      "params": {"stat_type": "deviation_from_run_mean"}
    }
  ]
}
```

### ML Models Table

**Key Fields:**
- `model_id` - Unique identifier
- `mlflow_run_id` - MLflow experiment run ID for model loading
- `feature_config_id` - Links to feature engineering configuration
- `status` - `training` | `production` | `archived`
- `training_metrics` - JSONB with model performance metrics

### Alert Rules (ML-specific)

**ML Rule Example:**
```sql
INSERT INTO alert_rules (
    name,
    detection_type,
    equipment_id,
    model_id,
    anomaly_threshold,
    severity,
    enabled
) VALUES (
    'ML Anomaly Detection',
    'ml',
    '550e8400-e29b-41d4-a716-446655440000',  -- Equipment UUID
    'b5c051b7-e211-44c0-9783-70d6032f2a4f',  -- Model UUID
    0.85,  -- Trigger when anomaly score >= 0.85
    'high',
    true
);
```

**Evaluation Process:**
1. Alert Service runs ML evaluation every 10 seconds
2. For each equipment with ML rules:
   - Query Feature Store for current sensor snapshot
   - Call ML Service `/api/inference/predict` endpoint
   - ML Service loads model from MLflow
   - Feature Engineering Engine applies transformations
   - Model produces anomaly score (0.0-1.0)
   - If score >= threshold → Create alert

---

## Manual Operations

### Create New Tenant

```sql
-- Insert company record
INSERT INTO public.companies (company_id, company_name)
VALUES ('550e8400-e29b-41d4-a716-446655440000', 'Example Tenant A');

-- Create tenant schema (auto-creates all tables)
SELECT create_tenant_schema(
    '550e8400-e29b-41d4-a716-446655440000'::UUID,
    'Example Tenant A'
);
```

### Grant Permissions to Existing Tenants

```sql
-- Run migration script manually if needed
\i 08-ml-alert-permissions-migration.sql
```

### Verify Tenant Schema

```sql
-- List all tenant schemas
SELECT schema_name
FROM information_schema.schemata
WHERE schema_name LIKE 'tenant_%'
ORDER BY schema_name;

-- Check tables in tenant schema
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;

SELECT table_name
FROM information_schema.tables
WHERE table_schema = 'tenant_550e8400_e29b_41d4_a716_446655440000'
ORDER BY table_name;

-- Verify permissions
SELECT grantee, privilege_type, table_name
FROM information_schema.table_privileges
WHERE table_schema = 'tenant_550e8400_e29b_41d4_a716_446655440000'
AND grantee = 'alert_service_user'
ORDER BY table_name, privilege_type;
```

---

## Troubleshooting

### Permission Denied Errors

**Symptom:** `permission denied for table feature_engineering_configs`

**Cause:** alert_service_user lacks SELECT permissions

**Fix:**
```sql
SET search_path TO tenant_550e8400_e29b_41d4_a716_446655440000, public;
GRANT SELECT ON feature_engineering_configs TO alert_service_user;
GRANT SELECT ON ml_models TO alert_service_user;
```

Or run migration script:
```bash
docker exec industryflow-timescaledb psql -U postgres -d industryflow \
  -f /docker-entrypoint-initdb.d/08-ml-alert-permissions-migration.sql
```

### Missing Tables in Tenant Schema

**Symptom:** Table does not exist after tenant creation

**Cause:** Script execution order issue or function not called

**Fix:** Re-run creation function:
```sql
SELECT add_feature_engineering_tables_to_schema('tenant_550e8400_e29b_41d4_a716_446655440000');
SELECT add_feature_config_columns_to_ml_models('tenant_550e8400_e29b_41d4_a716_446655440000');
```

### Column Does Not Exist

**Symptom:** `column "feature_config_id" does not exist`

**Cause:** ML models table not updated with new columns

**Fix:**
```sql
SELECT add_feature_config_columns_to_ml_models('tenant_550e8400_e29b_41d4_a716_446655440000');
```

---

## Version History

- **v5.0** - Schema-per-tenant architecture (Nov 7, 2025)
- **v5.1** - Feature engineering tables added (Nov 22, 2025)
- **v5.2** - ML alert integration permissions (Nov 23, 2025)

---

## References

- [Data & storage architecture](../../../docs/architecture/data-and-storage.md)
- [ML & feature engineering](../../../docs/architecture/ml-and-features.md)
- [Alerting](../../../docs/architecture/alerting.md)
