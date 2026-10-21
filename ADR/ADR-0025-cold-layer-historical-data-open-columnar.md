<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0025 — Cold layer for historical telemetry in an open columnar format

- **Status:** Accepted
- **Date:** 2026-07-08; changed 2026-10-21
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md) (framing)
- **Related:** [ADR-0003](ADR-0003-tenant-to-schema-resolution.md) (tenant mapping), [ADR-0009](ADR-0009-kubernetes-deployment-and-packaging.md) (scheduled jobs, object store), [ADR-0012](ADR-0012-notebook-credential-delivery.md) and [ADR-0015](ADR-0015-notebook-capability-minting-and-sql-proxy.md) (brokered reads, no secret in the kernel), [ADR-0032](ADR-0032-backup-and-disaster-recovery.md) (the cold layer's backup)

## Context

Raw telemetry lands in a hypertable in the transactional database, whose compression shrinks storage but
still streams rows over one connection. Training, search, backtests and drift monitoring reread long
history, so a history read's cost sets the pace of ML work. A compressed database chunk is readable only
by the database that wrote it.

## Decision

1. **Raw measurements are kept in Parquet in the object store, unbounded, for training and analysis.**
2. **Hot-store raw retention is the live-monitoring horizon; raw rows leave only through decision 3.**
3. **Export → verify → drop, ordered inside the exporter.** The drop locks writers off the day and
   proceeds only if a fingerprint of its rows (count and order-independent digest) equals the one taken
   at export. Each drop is recorded in its own transaction, so a crash resumes correctly.
4. **The export is idempotent, and archived parts are immutable.** Each run exports everything past the
   horizon not yet archived; late rows become a further part, and a part whose drop is recorded is never
   rewritten.
5. **Tenant isolation extends into the object store under the existing policy.** On write, tenants come
   from the registry and map through the one tenant mapping to their own prefix (ADR-0003). On read, the
   kernel holds no durable credential: a trusted broker resolves an audience-bound handle to one tenant
   and vends short-lived, read-only presigned reads within its prefix (ADR-0012, ADR-0015).
6. **Partition by event time; sort within each file by equipment.**
7. **The cold layer holds raw readings only; aggregates are recomputed on read.**
8. **Parquet now; a table format on a named trigger.** The trigger is a change to the archived reading's
   shape that readers cannot absorb by tolerating parts with and without a field, or a second writer; a
   new sensor or tenant only adds rows.
9. **Orchestration is a scheduled cluster job until a real dependency graph exists.**
10. **The object store is the one the platform already runs; the cold layer adds buckets and principals.**
11. **The exporter is a separate job whose principal can write but not delete in the cold bucket.** The
    stream's principal gains no access; the default engine is a single-node columnar writer.
12. **The layer is built; table format, distributed export and aggregate history wait for triggers.**
13. **The cold layer is mirrored by a principal that can only read it (ADR-0032 dec 4).** The mirror keeps
    an object one backup retention period after the cold layer no longer holds it.
14. **A tenant's cold objects are removed only by the tenant-delete job (ADR-0033 dec 4).** Its principal can
    list and delete in the cold bucket but neither write nor read objects, so no principal both writes and
    deletes the only copy of history.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Raw kept long in the hot store, queried directly | Row-wise transactional transport for analytical scans |
| 1 | Compressed chunks copied to the object store | A backup, not readable without the database that wrote it |
| 2 | A fixed retention timer dropping raw rows from the hot store | Drops rows whether or not a verified export holds them |
| 3 | Verify a drop by a row count reconciled at export | Misses changes after the export and changes that keep the count |
| 4 | A day archived once, in one file | A late reading for an exported day is either never archived or forces the day to be rewritten |
| 4 | Re-export a whole day when late rows arrive | Overwrites the only copy of history to add a few rows |
| 5 | One shared path keyed by a tenant column | Breaks schema-per-tenant isolation |
| 5 | A durable object-store key in the kernel | A transferable secret in untrusted code |
| 6 | Partition by equipment or sensor | Small-file directory explosion |
| 7 | Aggregates copied into the cold layer beside raw | Derivable state stored twice, free to drift |
| 8 | A table format from the start | Compaction and metadata work for problems not yet present |
| 9 | A heavyweight workflow scheduler for the export | Disproportionate to one linear daily task |
| 9 | Export inside the streaming job | Couples batch export failures to the latency-critical stream |
| 11 | An exporter principal that may also delete in the cold bucket | A faulty or compromised exporter could destroy the only copy of history |
| 12 | The cold layer built only when the first serious history read or a hot-store burden triggers it | Raw history kept accumulating in the hot store, and the read path is needed before the first large read |
| 13 | Mirroring by the exporter or under a credential that can delete | A mirror whose writer can delete does not survive the failure it guards against |
| 13 | An append-only mirror | A deleted tenant's raw history would never leave it (ADR-0033 dec 4) |
| 14 | Deletion by the exporter | Gives the cold layer's one writer the power to destroy history (dec 11) |

## Consequences

**Positive**
- History reads are parallel, columnar and pruned; tenant isolation holds; no new store or orchestrator.

**Negative**
- The cold layer is the only copy of old raw history; a further broker must honour revocation.
- A day may span several parts, readers must tolerate added fields, and the backup grows without bound.

## Deferred

| Open question | Settled by |
|---|---|
| Which table format | Decision 8's trigger |
| Volume at which the export becomes distributed | Measurement of the largest tenant's run |
| Long-horizon aggregate history | A need beyond hot retention |

## References

- [data-and-storage.md](../docs/architecture/data-and-storage.md), [cold-layer-evolution.md](../docs/architecture/cold-layer-evolution.md)
- `services/cold_export/`, `services/notebook_cold_broker/`, `infrastructure/backup/db-backup.sh`
