<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# Cold layer — deferred decisions, triggers & detection

[ADR-0025](../../ADR/ADR-0025-cold-layer-historical-data-open-columnar.md) built the cold layer
(exporter write path + notebook read path) but deliberately **deferred** several decisions to a
future *trigger* — building them now would be the overengineering the ADR warns against. This is
the operational companion to that ADR: for each deferred item it records whether the build has
since **resolved** it, and for the still-open ones the **sharp trigger**, the **observable signal**
that tells us the trigger fired (so the backlog can't rot unnoticed), and an **implementation
sketch**. Build nothing until its trigger fires; see the ADR for the *why*.

## Already resolved by the implementation

Two decisions ADR-0025 left open were settled when the layer was built:

- **Verification method (dec 3).** Chosen: **row-count reconciliation** — the source `COUNT(*)`
  for a day vs. the written Parquet footer `num_rows`, plus a per-day `_manifest.json` marker; a
  mismatch fails the run *before* the chunk is dropped. See `services/cold_export/exporter.py`.
- **Cold-store broker internals (dec 5, read side).** Chosen: a **standalone
  `notebook_cold_broker`** service vending **short-TTL, tenant-prefix-scoped pre-signed GET URLs**
  (not STS temp credentials, not folded into the SQL proxy), on a third `cold` capability audience.
  See `services/notebook_cold_broker/` and [notebooks.md](notebooks.md).

## Trigger-gated — watch these

### 1. Table format (Iceberg / Delta / Hudi) — dec 8

- **Trigger:** the raw schema must evolve *without rewriting history* (e.g. a new column on
  `sensor_measurements`), **or** parallel writers to the cold layer appear.
- **Signal to watch:** an init-script migration that ALTERs the columns of
  `tenant_<uuid>.sensor_measurements`; **or** the cold-export CronJob's `concurrencyPolicy` moving
  off `Forbid` / a second writer to the cold bucket. (A *new sensor* is a new row, not a new column
  — it does **not** trip this.)
- **Sketch:** stand up a catalog (REST / JDBC / Glue); have the exporter write an Iceberg table
  instead of bare Parquet (`pyiceberg`, or Spark-Iceberg if it lands with dec 11). Gains atomic
  commits, schema evolution, and snapshots; costs compaction, snapshot-expiry and orphan-file
  cleanup. Contained to the `ColdStore` adapter (`store.py`) + the catalog — the orchestration and
  the `company_id → prefix` mapping are unchanged.
- **Do not** adopt pre-trigger: commit-per-file metadata brings a small-file/compaction burden for
  problems we don't have (ADR alternative C).

### 2. Single-node → distributed Spark batch export — dec 11

- **Trigger:** one tenant's per-run export volume outgrows a single node (wall-clock or memory).
- **Signal to watch:** the cold-export CronJob's per-run **duration approaching the schedule
  interval**, or memory **near its limit** (`coldExport.resources.limits`, default 2Gi), or a
  per-tenant-per-day row count crossing ~O(10⁸)/day (the hundreds-of-MB file target badly
  exceeded). The exporter already logs `Verified cold export: <schema> <day> (N rows)` per day —
  the cheap detection step when the export runs in prod is a Prometheus alert on the CronJob's
  duration/memory (the platform already runs kube-prometheus-stack).
- **Sketch:** swap the single-node PyArrow adapters for a Spark batch job (JDBC read from Postgres →
  partitioned Parquet), keeping the **same** export → verify → drop contract and canonical prefix.
  The `MeasurementSource` / `ColdStore` **ports** (`ports.py`) mean the orchestration (`exporter.py`)
  is untouched — only the adapters change.
- **Do not** build Spark pre-trigger: a single node matches the one-linear-task shape at this scale
  (ADR dec 9/11).

### 3. Concrete raw-retention horizon — "concrete retention values"

- **Not a trigger — a config value.** Current: `COLD_EXPORT_HORIZON_DAYS=30`
  (`coldExport.horizonDays` / `.env`). The exporter is the *sole* drop authority
  (export → verify → drop); there is **no** blind TimescaleDB retention policy on raw, so the
  effective raw retention *is* this horizon.
- **Revisit when:** the live-monitoring / post-alert-forensics window operators actually query
  changes; or the raw hot-store size / disk pressure warrants a shorter horizon.
- **Signal to watch:** the TimescaleDB raw-hypertable size and node disk usage (postgres-exporter
  metrics). **Irreversible past the horizon** once the exporter runs — older raw exists only in the
  cold layer.

## Out of scope (their own decisions)

- **Long-horizon _aggregate_ history (dec 7).** A separate ADR *if* a reader ever needs aggregates
  beyond the tiered hot-layer retention (90d/180d/1yr). Do **not** copy aggregates into the cold
  layer — they are derivable from raw (ADR alternative D).
- **Prefect / ML-pipeline orchestration (dec 9).** IndustryGrow Phase-2's concern, when a real
  feature-prep → train → validate → register dependency graph exists — not the cold export's.

## Trigger-watch quick reference

| Deferred item | Trigger | Observable signal | Status |
|---|---|---|---|
| Verification method | — | — | **resolved**: row-count + manifest |
| Broker internals | — | — | **resolved**: standalone broker, pre-signed URLs |
| Table format (Iceberg/…) | schema-evolve-no-rewrite OR parallel writers | `sensor_measurements` column migration; concurrency off `Forbid` | open, gated |
| Single-node → Spark | per-run volume > one node | export duration → schedule interval; mem → limit; rows/day ~10⁸ | open, gated |
| Raw-retention horizon | (config) cost / disk / query-window change | raw hypertable size, node disk | open, tunable now (`COLD_EXPORT_HORIZON_DAYS`) |
| Long-horizon aggregate history | a reader needs >tiered aggregate retention | a feature request | out of scope (own ADR) |
| Prefect / ML-pipeline | a real ML dependency graph (IndustryGrow P2) | that project starting | out of scope (own project) |
