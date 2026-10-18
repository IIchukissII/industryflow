<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0006 — Spark windowing and idempotent writes

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-10-18
- **Parent:** ADR-0001
- **Related:** ADR-0005 (at-least-once), ADR-0023 (features), ADR-0029 (checkpoint cutover), ADR-0009 (Kubernetes)

## Context

Two Spark jobs write sensor readings to the time-series store: one lands raw measurements, one
computes windowed aggregates. Delivery is at-least-once (ADR-0005), so every write must be safe to
repeat. Unbounded window state exhausts memory, and model features are computed over the same windows.

## Decision

1. **Windowed aggregations declare a watermark.** State is bounded by the watermark horizon, not by runtime.
2. **Each window is written once, when it closes.** The store holds one row per window and series.
3. **Writes are idempotent on their natural key: raw measurements first-write-wins, aggregates
   replace-on-conflict.** A redelivered or forged message cannot rewrite a recorded reading, while
   the latest computation of a window is kept.
4. **Only infrastructure failures fail the batch; a reading no retry can write is dead-lettered.** A
   malformed, unknown-tenant or unknown-device, or far-future reading goes to the destination of
   ADR-0005 dec 3, by one shared rule applied before the watermark. The aggregation excludes the same
   readings without dead-lettering them again, and no write error is swallowed.
5. **Checkpoints live on durable storage that every node running the query can reach.** With
   executors on several nodes that is the object store; in a single process, a durable single-writer
   volume.
6. **A failed query takes the process down.** The restart policy and health check then observe it.
7. **Window width and allowed lateness are a feature contract with one shared definition.** The
   writer, the feature server and offline training read them from the shared library, and a check
   refuses any restatement.
8. **The aggregation's state partition count is part of its checkpoint's identity, and it is sized to
   the series count, not to the cluster.** The engine fixes the count when a checkpoint is created and
   ignores a new value afterwards, so a changed count starts fresh checkpoints rather than doing
   nothing. Each batch commits state per partition, so the count sets the job's fixed per-batch cost.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Store retention instead of a watermark | Bounds stored rows, not executor state |
| 1 | Larger executors to survive unbounded state | Postpones the out-of-memory failure; unbounded state defeats any fixed ceiling |
| 2 | Re-emit open windows and deduplicate downstream | Partial rows for every reader; state still unbounded |
| 3 | Delete-then-insert a window | Not atomic under retries and can briefly expose a window as missing |
| 3 | Replace-on-conflict for raw measurements | A later message with the same key rewrites a recorded fact |
| 4 | Different error policies per job, the aggregation continuing past a write error | Silent data loss under at-least-once delivery |
| 4 | Fail the batch on every write error and let an operator remove the bad message | One poison reading halts every tenant and returns after every restart |
| 5 | A persistent volume per query, with the storage medium left to deployment | Node-local storage cannot serve a state store written on several nodes |
| 5 | A per-node checkpoint volume with each stateful query pinned to one node | Forbids a horizontal worker pool and strands the checkpoint with a lost node |
| 7 | Window width and allowed lateness set per deployment | A deployment change silently changes the meaning of every trained feature |
| 7 | Window values copied per component and tested equal | A copy the test does not list drifts |
| 8 | Change the count in place on an existing checkpoint | The engine silently keeps the old count; the setting does nothing |
| 8 | Refuse to start when the configured and checkpointed counts differ | Reads engine-internal checkpoint metadata, and still leaves the operator to reset by hand |
| 8 | Keep a count sized for cluster parallelism (8, or the engine default of 200) | Per-batch state commits then dominate CPU on a small host, whatever the data rate |

## Consequences

**Positive**
- Bounded state, one correct row per window, no duplicates or rewrites, and no stall on a bad reading.
- Writer, server and trainer cannot disagree about windows, and a restart resumes from committed state.

**Negative**
- Data later than the allowed lateness is dropped, an aggregate appears only after its window closes,
  and dead letters need watching.
- A raw correction is an explicit operation outside the stream; changing a window value invalidates trained models.
- A scaled-out streaming path depends on the object store.
- Changing the state partition count costs each series' open window (written from post-change
  readings only) and leaves the old checkpoint to delete.

## Deferred

| Open question | Settled by |
|---|---|
| Late data beyond the watermark: drop or route to a correction path | First use case needing late readings |

## References

- [docs/architecture/stream-processing.md](../docs/architecture/stream-processing.md); `libs/industryflow_contracts/` (decision 7).
