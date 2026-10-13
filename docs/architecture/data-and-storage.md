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
orchestrator: for each tenant it reads a day from one database snapshot, writes it as a Parquet
part, reconciles the written row count against the source, lists the part in the day's
`_manifest.json`, and only then drops the chunk. The drop re-reads the day under a lock and drops
nothing if any row arrived, changed or left since the snapshot (a row-count-and-digest check in
`cold_export_drop_day`); the next run exports it. It is idempotent and self-healing — "export
everything older than N days not yet exported" — so a failed run re-picks the same days next time.
Runs never overlap, whatever starts them: a run holds a database advisory lock for its whole
duration, and a run that finds it held logs that and exits successfully having done nothing (the
run in progress is doing its work). The lock dies with the holder's session, so a crashed run
never blocks the next.

A verified part is never rewritten. Rows that arrive for a day after it was dropped land in a new
chunk and are exported as a further part (`measurements-0001.parquet`, …) with its own reconciled
count; a row whose natural key the archive already holds is not exported twice. The manifest lists
every verified part of the day, and readers read exactly the listed parts. Whether a listed part's
drop committed is recorded by the database in the drop's own transaction
(`public.cold_export_drops`), so a crash on either side of the drop resumes correctly.

Isolation carries into the object store under the *existing* policy, not a new one:

- **Write side (ADR-0003).** Tenants come from the verified `public.companies` registry; each
  tenant's Parquet lives under its own `tenant_<uuid>/…` prefix via the single canonical
  `company_id → prefix` mapping (`services/cold_export/naming.py`) — never a shared path keyed by
  a column. The schema it reads and drops is derived from the `company_id` by the canonical
  mapping; a registry `schema_name` that disagrees is refused, not used. The exporter connects as the least-privilege `cold_export_user` (SELECT on raw only)
  and drops chunks through a `SECURITY DEFINER` function, so it never owns the table. Its
  object-store principal is **write-scoped** to the cold bucket (put/get/list), never the root
  MinIO keys.
- **Read side (ADR-0012 / ADR-0015).** Notebooks read the cold layer through the
  **cold-store broker** (`services/notebook_cold_broker/`), the object-store twin of the SQL
  proxy. The authoring kernel holds no durable object-store credential — only a per-session
  **cold capability** handle (`INDUSTRYFLOW_COLD_CAPABILITY`, minted by the spawner). It presents
  that handle to the broker, which resolves it to one tenant, confines every key to that tenant's
  `tenant_<uuid>/` prefix, and returns short-TTL, **read-only** pre-signed GET URLs so bytes flow
  directly kernel↔object store (in parallel, column/partition-pruned). The broker holds a
  **read-only** cold-bucket principal — distinct from the exporter's write principal and the root
  keys. The `industryflow.IndustryFlowCold` client wraps it (`list_files` / `read_parquet` /
  `read_all`). A data scientist on tenant A can no more read tenant B's Parquet than tenant B's
  schema.

Where it lives: the exporter in `services/cold_export/` (`exporter.py` holds the ordering
invariant, `naming.py` the one tenant-prefix mapping); the role, the `SECURITY DEFINER`
`cold_export_drop_day` and the removal of the blind raw retention policy in init script
`16-cold-layer-raw-retention.sql` (with matching grants in `04-complete-tenant-creation.sql`,
`00-create-roles.sh` and `pg_hba.conf`); the broker in `services/notebook_cold_broker/`; the
`cold` audience in `services/notebook_hub/capabilities.py`; the CronJob in the Helm chart
(`cronjob-cold-export.yaml`) and the compose `cold-export` profile; the cold bucket and its
write-scoped and read-only principals are created by the object store's bootstrap job, the one
place that holds the store's root credential.

ADR-0025 deliberately deferred parts of the cold layer to a future trigger (a table format, a
distributed Spark exporter, the concrete retention horizon). Those triggers, the signals that
detect them, and what to build when they fire are tracked in
[cold-layer-evolution.md](cold-layer-evolution.md) — build nothing until its trigger fires.

## Roles & grants

Each service that touches the database connects as its own least-privilege role
(`api_gateway_user`, `spark_streaming_user`, `alert_service_user`, `ml_service_user`,
`cold_export_user`), and `create_tenant_schema()` grants each role only the access it needs
within every tenant schema (e.g. Spark insert, the gateway full CRUD, cold-export SELECT on raw
plus EXECUTE on `cold_export_day_fingerprint` and `cold_export_drop_day`). Ingestion is mTLS-only and holds no database role at
all — it produces to Kafka and Spark performs the writes (ADR-0002). Role connection limits
apply — size service connection pools (workers × pool size) within them.
