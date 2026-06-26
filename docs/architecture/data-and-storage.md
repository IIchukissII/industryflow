<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Data & storage

How IndustryFlow stores tenant data: schema-per-tenant isolation on PostgreSQL + TimescaleDB,
with compressed time-series hypertables. The SQL under
`infrastructure/timescaledb/init-scripts/` is authoritative for the schema; this is the shape.

## Multi-tenancy: schema-per-tenant

Each tenant (company) owns a dedicated PostgreSQL **schema** named `tenant_<uuid>` (the
company UUID with dashes replaced by underscores). A shared `public.companies` registry maps
`company_id` → `schema_name`. Tenant tables (equipment, sensors, measurements, aggregations,
alerts, ML models, feature configs) live inside each tenant schema; the `public` schema holds
only the cross-tenant registry and the auth `user` table.

**Why schemas, not row-level security:** strong isolation without an RLS predicate on every
query, and clean per-tenant lifecycle (create/drop a schema). Tenants are **provisioned at
runtime** — nothing is hardcoded; `scripts/create-tenant.sh` inserts the company row and calls
`create_tenant_schema()`, which builds the schema and all its tables.

A request's tenant is resolved from a **verified** identity, never the request body:

- API calls — the `company_id` **JWT claim** (validated as a UUID), so there is no per-request
  schema scan.
- Device ingestion — the `company_id` in the **client-certificate SAN**.

The resolved UUID is validated and applied with `SET LOCAL search_path TO tenant_<uuid>,
public` **inside a transaction**, so the tenant path never lingers on a pooled connection.
See **[ADR-0003](../../ADR/ADR-0003-tenant-to-schema-resolution.md)**.

## Tables (per tenant schema)

- `equipment`, `sensors` — the asset model (generic; domain entities live in
  [extension-owned tables](../operations/extensions.md), not as columns here).
- `sensor_measurements` — raw readings, a TimescaleDB **hypertable**, `UNIQUE(time, sensor_id)`
  for idempotent writes.
- `sensor_aggregations_1min` / `_5min` / `_1hour` — rolled-up windows, hypertables with
  `UNIQUE(time, sensor_id, equipment_id)` (the unique key includes the partition column).
- `alert_rules`, `alerts` — alerting configuration and history.
- `ml_models`, `model_predictions`, `feature_engineering_configs` — the ML surface.

## Time-series: hypertables, compression, retention

`sensor_measurements` and the aggregation tables are **hypertables** — TimescaleDB partitions
them into time chunks transparently, which keeps inserts fast and time-range queries pruned to
the relevant chunks.

Older chunks are **compressed** columnar (segment-by `sensor_id`, order-by `time`), which
shrinks high-volume sensor data substantially and speeds scans over compressed ranges; recent
chunks stay uncompressed for fast writes. Compression and any retention policies are configured
per the init scripts / TimescaleDB policies — tune the compress-after and drop-after windows to
the deployment's storage and query needs.

## Roles & grants

Each service connects as its own least-privilege role (`api_gateway_user`,
`ingestion_service_user`, `spark_streaming_user`, `alert_service_user`, `ml_service_user`), and
`create_tenant_schema()` grants each role only the access it needs within every tenant schema
(e.g. ingestion/Spark insert, the gateway full CRUD). Role connection limits apply — size
service connection pools (workers × pool size) within them.
