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

Older chunks are **compressed** columnar (segment-by `equipment_id`, order-by `time`), which
shrinks high-volume sensor data substantially and speeds scans over compressed ranges; recent
chunks stay uncompressed for fast writes. Compress-after and the aggregation tables' tiered
retention (90d / 180d / 1yr) are configured per the init scripts.

**Raw retention is owned by the cold layer, not a blind timer.** There is deliberately *no*
TimescaleDB `add_retention_policy` on `sensor_measurements`: a timer would drop chunks whether or
not they were archived. Instead the cold-export job is the sole authority that drops a raw chunk,
and only after a verified Parquet export (see below). The effective raw-retention horizon is the
export horizon (`COLD_EXPORT_HORIZON_DAYS`, default 30 days). See
**[ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md)**.

## Cold layer: long-horizon history (ADR-0025)

Three storage layers answer three different questions: the **stream** (Spark state) answers
"is an anomaly happening now?", the **hot** store (TimescaleDB) answers "what happened in the
last hours/days?", and the **cold** layer answers "give me years, all columns — I am training."
Reading years of raw row-wise over the Postgres wire is OLTP transport for an OLAP scan; the cold
layer stores the same raw content as partitioned **Parquet** in the object store, read in
parallel with column and partition pruning.

The exporter (`services/cold_export/`) is a single-node columnar writer — **not** Spark — run as
a Kubernetes CronJob (on the box: `docker compose --profile cold-export up cold-export`). Its one
invariant is the ordering **export → verify → drop**, enforced in the script, never in the
orchestrator: for each tenant it writes a day's Parquet, reconciles the written row count against
the source, and only then drops the chunk. It is idempotent and self-healing — "export everything
older than N days not yet exported" — so a failed run re-picks the same days next time (a
per-day `_manifest.json` is the "verified" marker).

Isolation carries into the object store under the *existing* policy, not a new one:

- **Write side (ADR-0003).** Tenants come from the verified `public.companies` registry; each
  tenant's Parquet lives under its own `tenant_<uuid>/…` prefix via the single canonical
  `company_id → prefix` mapping (`services/cold_export/naming.py`) — never a shared path keyed by
  a column. The exporter connects as the least-privilege `cold_export_user` (SELECT on raw only)
  and drops chunks through a `SECURITY DEFINER` function, so it never owns the table. Its
  object-store principal is **write-scoped** to the cold bucket (put/get/list), never the root
  MinIO keys.
- **Read side (ADR-0012 / ADR-0015).** Notebooks read the cold layer through a brokered,
  no-durable-secret, single-tenant path — the object-store twin of the SQL proxy. This read path
  is **deferred** (a follow-up slice); today's implementation is the write path only.

## Roles & grants

Each service that touches the database connects as its own least-privilege role
(`api_gateway_user`, `spark_streaming_user`, `alert_service_user`, `ml_service_user`,
`cold_export_user`), and `create_tenant_schema()` grants each role only the access it needs
within every tenant schema (e.g. Spark insert, the gateway full CRUD, cold-export SELECT on raw
plus EXECUTE on `cold_export_drop_day`). Ingestion is mTLS-only and holds no database role at
all — it produces to Kafka and Spark performs the writes (ADR-0002). Role connection limits
apply — size service connection pools (workers × pool size) within them.
