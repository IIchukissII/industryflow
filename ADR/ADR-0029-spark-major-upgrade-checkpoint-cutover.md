<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0029 — Spark major upgrades: resume the checkpoint when it can be read, reset it when it cannot

- **Status:** Accepted
- **Date:** 2026-07-15; changed 2026-10-17
- **Parent:** [ADR-0006](ADR-0006-spark-windowing-and-idempotent-writes.md) (idempotent sinks, bounded state, durable checkpoints)
- **Related:** [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (at-least-once, so re-reading the source is safe), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (per-image smoke and box run), [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (aggregate tables that see a gap on reset), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (cold layer, untouched by either path)

## Context

Structured-streaming state (offset logs, commit logs and the windowed state store of the aggregation
job) is a private format that Spark does not promise is portable across a major version. A major
upgrade therefore raises a data-correctness question: can the new version resume the old checkpoint,
and if not, what happens to the state in it. ADR-0006 left a lost checkpoint as its own recovery
concern.

The sinks are idempotent on their natural key, so the checkpoint is an optimisation that lets a
restart skip re-reading, not the system's source of truth. Whether a given boundary resumes is known
only by running it; on 3.5 → 4.1 the checkpoint resumes, offsets and state both.

## Decision

1. **A Spark major upgrade rests on the idempotent sinks, not on checkpoint portability.** Writes are
   idempotent (ADR-0006 dec 3) and delivery is at-least-once (ADR-0005), so re-deriving a stream from
   the source is safe and the upgrade is correct whether the checkpoint is resumed or reset.
2. **The checkpoint is resumed when the new version can read it, established empirically per boundary
   and never assumed.** The box run (ADR-0026 dec 6) decides. For 3.5 → 4.1 it resumes, so that upgrade
   is a restart against the existing checkpoint with no loss.
3. **A reset is the fallback for a boundary that does not resume, at a bounded, asymmetric cost.** The
   stateless job re-reads and re-upserts losslessly within broker retention; the stateful job restarts at
   the tail and loses only the windows open at the reset.
4. **The upgrade is proven by the per-image smoke (ADR-0026 dec 5) and the box run, not the whole-stack
   gate, which excludes the Spark JVMs on cost.** It is an operator-run act with a documented procedure.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Drop the checkpoint on every major | Discards resumable offsets and live window state; the reset is the fallback, not the default |
| Assume resume and skip verification | A boundary that silently reinitialises state or refuses to start is found in production |
| Migrate the old state store into the new format by hand | No supported path; a "successful" migration could be silently wrong, and the reset is already safe |

## Consequences

**Positive**
- The upgrade is correct by construction, and cheap when the checkpoint resumes.
- ADR-0006's lost-checkpoint concern has an answer: a reset is safe, and its per-job cost is named.
- The cold layer is unaffected by either path; it reads the hot store, which neither removes.

**Negative**
- Every major must be run before it is trusted; resume on one boundary does not carry to the next.
- A forced reset leaves a gap in the aggregate tables (ADR-0023), and in drift monitoring that reads
  them (ADR-0021), for the windows open at the reset.
- The upgrade needs a maintenance window: the jobs stop and restart.

## Deferred

| Open question | Settled by |
|---|---|
| Automating the upgrade (the resume-or-reset choice and the restart) | When the cadence of Spark majors makes the manual run a burden |
| A state format that survives majors (external state store or documented migration) | A boundary that fails to resume and a reset loss that is no longer acceptable |

## References

- [docs/architecture/stream-processing.md](../docs/architecture/stream-processing.md): checkpoint
  locations and the upgrade procedure.
- Issue #243: the Spark 3.5 → 4.1 migration whose box run established resume.
