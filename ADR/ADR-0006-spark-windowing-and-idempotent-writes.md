<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0006 — Spark windowing and idempotent writes

- **Status:** Accepted
- **Date:** 2026-06-26; changed 2026-11-13
- **Parent:** ADR-0001
- **Related:** ADR-0005 (at-least-once), ADR-0023 (features), ADR-0029 (checkpoint cutover), ADR-0009 (Kubernetes)

## Context

Two Spark jobs write sensor readings to the time-series store: one lands raw measurements, one
computes windowed aggregates. Delivery is at-least-once (ADR-0005), so every write must be safe to
repeat. Unbounded window state exhausts memory, and model features are computed over the same windows.
The engine keeps window state in a private format that only a query of the same aggregation and the
same state partition count can resume; it fixes the count when the state is first written, ignores a
new value afterwards, and commits state per partition on every batch.

## Decision

1. **Windowed aggregations declare a watermark.** State is bounded by the watermark horizon, not by runtime.
2. **Each window is written once, when it closes.** The store holds one row per window and series.
3. **Writes are idempotent on their natural key: raw measurements first-write-wins, aggregates
   replace-on-conflict.** A redelivered or forged message cannot rewrite a recorded reading, while
   the latest computation of a window is kept.
4. **Only infrastructure failures fail the batch; a reading no retry can write is dead-lettered.** A
   malformed, unknown-tenant or unknown-device, or far-future reading goes to the destination of
   ADR-0005 dec 3, by one shared rule. The aggregation excludes the same readings without
   dead-lettering them again: those judged from the reading alone before the watermark, those naming
   an unknown or inactive tenant, sensor or equipment at write; no write error is swallowed.
5. **Checkpoints live on durable storage that every node running the query can reach.** With
   executors on several nodes that is the object store; in a single process, a durable single-writer
   volume.
6. **A failed query takes the process down.** The restart policy and health check then observe it.
7. **Window width and allowed lateness are a feature contract with one shared definition.** The
   writer, the feature server and offline training read it, and none restates it.
8. **The aggregation's checkpoint identity is its state partition count and its state shape, and
   changing either is a deliberate reset, never a side effect.** A changed count would otherwise do
   nothing and a changed shape stop every aggregation query; a reset costs what ADR-0029 dec 3 states.
   The count is sized to the series count, not to the cluster, since it sets the job's fixed per-batch
   cost.
9. **A window's spread is its sample standard deviation, and zero for a window of one reading.** The
   zero is the statistic's defined value, not a substitute (ADR-0044 dec 3). Every window then carries a
   spread a consumer can compute on; a single reading shows no variation, and
   the window's reading count still tells it from a steady signal. Training re-derives it by the same
   rule (ADR-0023 dec 9), and the rule leaves the aggregation's state shape as it is (dec 8).

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
| 4 | Every exclusion before the aggregation's watermark | The registry checks are a lookup per tenant per batch, made at write; only a far-future time moves the watermark wrongly, and that check runs before it |
| 5 | A persistent volume per query, with the storage medium left to deployment | Node-local storage cannot serve a state store written on several nodes |
| 5 | A per-node checkpoint volume with each stateful query pinned to one node | Forbids a horizontal worker pool and strands the checkpoint with a lost node |
| 7 | Window width and allowed lateness set per deployment | A deployment change silently changes the meaning of every trained feature |
| 7 | Window values copied per component and tested equal | A copy the test does not list drifts |
| 8 | Change the count in place on an existing checkpoint | The engine silently keeps the old count; the setting does nothing |
| 8 | Change the aggregation in place on an existing checkpoint | The deployed checkpoint can no longer be read and every aggregation query stops |
| 8 | Refuse to start when the configured and checkpointed counts differ | Reads engine-internal checkpoint metadata, and still leaves the operator to reset by hand |
| 8 | Keep a count sized for cluster parallelism (8, or the engine default of 200) | Per-batch state commits then dominate CPU on a small host, whatever the data rate |
| 9 | No spread for a window of one reading | Every score of a sensor reporting about once per window is degraded, and its drift is never computed (ADR-0044 dec 3) |
| 9 | Population standard deviation for every window | Changes the spread of every window and every model trained on it, to settle one case |

## Consequences

**Positive**
- Bounded state, one correct row per window, no duplicates or rewrites, and no stall on a bad reading.
- Writer, server and trainer cannot disagree about windows, and a restart resumes from committed state.

**Negative**
- Data later than the allowed lateness is dropped, an aggregate appears only after its window closes,
  and dead letters need watching.
- A raw correction is an explicit operation outside the stream; changing a window value invalidates trained models.
- A scaled-out streaming path depends on the object store.
- A window of one reading reads as zero spread; telling it from a steady signal takes its reading count.
- Changing the state partition count or shape is a reset at the cost of ADR-0029 dec 3, and leaves the
  old checkpoint to delete.

## Deferred

| Open question | Settled by |
|---|---|
| Late data beyond the watermark: drop or route to a correction path | First use case needing late readings |

## References

- [docs/architecture/stream-processing.md](../docs/architecture/stream-processing.md): manual of decisions 1-9.
