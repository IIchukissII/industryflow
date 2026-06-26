<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: MIT
-->

# ADR-0006: Spark windowing and idempotent writes — bounded state, write-once aggregates, fail-fast batches

- **ID:** ADR-0006
- **Status:** Proposed
- **Date:** 2026-06-26
- **Project:** IndustryFlow
- **Parent:** ADR-0001 (framing — pending)
- **Companions:** ADR-0005 (Kafka delivery semantics)

## Context and problem

The Spark layer reads sensor readings from Kafka and writes them to TimescaleDB in two jobs: a streaming job that lands raw measurements, and an aggregation job that computes windowed roll-ups (1 min / 5 min / 1 hour). The aggregation job, as written, has defects that make it both unstable and incorrect, and the two jobs disagree on how to handle failure.

- **Unbounded streaming state.** The aggregation job groups by time window with `outputMode("update")` but never sets a watermark (`services/spark_jobs/kafka_aggregations.py:157-163`). Without a watermark Spark must retain the aggregation state for *every* window forever, because it can never decide a window is closed. State grows without bound and the executors eventually OOM. This is the single biggest stability risk in the pipeline.
- **Duplicate aggregate rows.** In `update` mode every micro-batch re-emits each still-open window with its current partial value, and the writer appends those rows to TimescaleDB with no upsert (`kafka_aggregations.py:191-199`). A one-hour window for one sensor stays open and is re-appended every few seconds — on the order of hundreds of partial/duplicate rows for a single real window. Every downstream query then reads garbage.
- **Silently swallowed write failures.** On a DB write error the streaming job re-raises (failing the batch so Spark retries), but the aggregation job catches the exception, logs it, and continues (`kafka_aggregations.py:103-118` vs `kafka_to_timescaledb.py:120-122`). Aggregation write failures are therefore lost permanently while the stream reports success — silent data loss, and an inconsistent policy between two jobs that should behave the same.
- **Ephemeral checkpoints.** The query checkpoint location falls back to container-ephemeral `/tmp`, so a restart cannot recover offsets/state and reprocesses or loses position.

These are facets of one missing decision: how windowed aggregation bounds its state, how often and how idempotently it writes, and what a failed batch does. ADR-0005 made the pipeline at-least-once, which means redelivery and batch retries are now *expected*; that guarantee is only safe if the Spark sinks are idempotent, which today they are not. This ADR decides the windowing and write discipline that makes the Spark layer both correct and a well-behaved at-least-once consumer.

## Decision drivers

- **Streaming state must be bounded.** An aggregation that retains every window forever is an outage waiting to happen; a watermark is what lets Spark close and evict old windows.
- **An aggregate row must be written once, not once per micro-batch.** The store should hold one row per (window, series), not the running partial re-emitted hundreds of times.
- **At-least-once delivery requires idempotent writes.** Because ADR-0005 guarantees redelivery and Spark retries batches, a write must be safe to repeat without creating duplicates.
- **Failure must stop the batch, uniformly.** A write error must fail the batch so offsets are not committed and Spark retries — the same policy in both jobs. Swallowing the error is the silent-loss failure ADR-0005 exists to forbid, reappearing at the sink.
- **Restarts must recover.** Checkpoints must survive container restarts so the job resumes from its committed position rather than reprocessing from scratch or losing place.

## Decision

1. **Windowed aggregations declare a watermark.** Every windowed aggregation sets a watermark on the event-time column so Spark can close windows past the allowed lateness and evict their state. Aggregation state is thereby bounded by the watermark horizon, not by total runtime (removes the unbounded-state OOM).

2. **Each window is written once, when it closes.** Aggregations emit a window's final value once (append semantics on watermark-closed windows) rather than re-emitting the running partial every micro-batch. The store holds one row per (window, series), not the per-batch partials (removes the duplicate-row defect).

3. **Writes are idempotent.** Aggregate (and measurement) writes are idempotent on their natural key — `(time/window, sensor_id, equipment_id)` — via upsert / `ON CONFLICT DO UPDATE` (or an equivalent merge), so a retried batch or a redelivered message (expected under ADR-0005) does not create a duplicate row. The aggregation tables carry the corresponding unique constraint.

4. **A write failure fails the batch — uniformly across both jobs.** On a sink error the batch raises, so Spark does not commit offsets and retries it; neither job swallows write exceptions. The streaming and aggregation jobs share this fail-fast policy. Combined with decision 3, a retried batch is safe because the writes are idempotent.

5. **Checkpoints live on durable storage.** Each streaming query's checkpoint location is a persistent volume, not container-ephemeral `/tmp`, so a restart resumes from the committed offsets and state. The checkpoint location is configured explicitly rather than relying on a default.

6. **A failed query takes the process down.** The jobs await all streaming queries (e.g. `awaitAnyTermination`) so that if any query dies the process exits and the container's restart policy and healthcheck observe it, rather than one dead query being masked by a still-running sibling.

## Alternatives considered

**A. Keep `update` mode and deduplicate aggregate rows downstream.** *Rejected:* it pushes the hundreds-of-partials-per-window problem onto every reader and onto storage, and still leaves state unbounded without a watermark. Writing each window once at the source is cheaper and correct; cleaning up after a known-bad write pattern is not.

**B. Bound storage growth with a TTL/retention policy instead of a watermark.** *Rejected:* retention bounds what is *stored*, not the *Spark streaming state*. The OOM is in the executor's in-memory aggregation state, which only a watermark evicts; a DB retention policy does nothing for it.

**C. Make writes safe by deleting-then-inserting the window.** *Rejected:* delete+insert is not atomic under retries and concurrent batches and can briefly expose a window as missing; an upsert on the natural key is atomic and idempotent without that window.

**D. Larger executors / more memory to survive the unbounded state.** *Rejected:* it postpones the OOM rather than removing it; unbounded state defeats any fixed memory ceiling. The fix is to bound the state, not to raise the ceiling.

**E. Leave the two jobs with different error policies (stream fail-fast, aggregation continue).** *Rejected:* the aggregation "continue" policy is silent data loss and contradicts ADR-0005. Both jobs are at-least-once consumers and must fail the batch on a write error so it is retried.

## Consequences

### Positive

- Aggregation state is bounded by the watermark, removing the unbounded-growth OOM — the pipeline's largest stability risk.
- The store holds one correct row per window, so downstream queries and the aggregation tests see real values instead of hundreds of partials.
- The Spark sinks are idempotent, which is what makes ADR-0005's at-least-once guarantee safe end-to-end: retries and redeliveries no longer duplicate data.
- Failures are visible and recoverable: a write error retries instead of vanishing, a dead query takes the process down to be restarted, and a restart resumes from durable checkpoints.

### Negative

- A watermark means data later than the allowed lateness is dropped from its window — a deliberate correctness/completeness trade that must be tuned per window (the late-data policy is deferred below).
- Idempotent upserts require a unique constraint on the aggregation tables and are somewhat more expensive per write than blind appends.
- Write-once-on-close raises end-to-end latency for an aggregate: a window's row appears when the window closes (plus watermark lag), not continuously as it fills.
- Durable checkpoints need a provisioned, backed-up volume; a corrupted or lost checkpoint becomes its own recovery concern.

## Deferred decisions

- **Watermark horizon and window sizes.** The exact allowed-lateness per window and the window definitions (1 min / 5 min / 1 hour) are configuration owned by the jobs/`.env`, not fixed here.
- **Late-data handling.** Whether data beyond the watermark is simply dropped or routed to a side output / late-correction path is left open.
- **Checkpoint storage medium.** The concrete durable location (named volume, object store, etc.) is an infrastructure/compose decision.
- **Streaming vs cluster execution.** Whether these jobs continue to run `local[*]` or submit to the provisioned Spark cluster is a separate deployment decision (noted in the review) and is not decided here.

## References

- IndustryFlow review (2026-06-26): aggregation job missing watermark (unbounded state/OOM), `update`-mode append producing duplicate rows, swallowed write failures, ephemeral `/tmp` checkpoints — internal report.
- ADR-0005 — Kafka delivery semantics; this ADR provides the idempotent sinks that make at-least-once safe.
