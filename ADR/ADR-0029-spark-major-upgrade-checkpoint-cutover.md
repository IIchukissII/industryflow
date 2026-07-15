<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0029 — Upgrading Spark across a major: the checkpoint is dropped, not migrated

- **ID:** ADR-0029
- **Status:** Proposed
- **Date:** 2026-07-15
- **Project:** IndustryFlow
- **Parent:** [ADR-0006](ADR-0006-spark-windowing-and-idempotent-writes.md) (bounded state, idempotent writes, durable checkpoints — the guarantees this cutover must not break)
- **Companions:** [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (at-least-once, the property that makes a checkpoint drop safe), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (where this upgrade is proven, and where it is deliberately *not*)
- **Related:** [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (the aggregate tables that see a bounded gap at cutover), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (the cold layer, which does not)

## Context and problem

Spark 4 is a major version, and structured-streaming state — the offset logs, commit logs, and the
windowed *state store* the aggregation job keeps — is a private format Spark does not promise is portable
across a major. A checkpoint written by 3.5 may not be readable by 4.1; when it is not, the job either
refuses to start or, worse, silently reinitialises state and reports success anyway. That is a
data-correctness question, and it decides the whole migration (#243) before any Scala/JDK/JAR work is
worth doing.

ADR-0006 stopped one step short of here. It made the sinks idempotent, bounded the state with a
watermark, and put the checkpoint on durable storage so *a restart resumes from committed offsets and
state* — then named the hole in its own Consequences: *"a corrupted or lost checkpoint becomes its own
recovery concern."* A major-version upgrade is that concern arriving on purpose. What ADR-0006 left
unrecorded is what recovery *is*.

The question, then, is not "how do we carry a 3.5 checkpoint into 4.1" — Spark offers no way to guarantee
that. It is: **when the checkpoint cannot come across, is re-deriving from the source safe, and how far
does "safe" reach?** It does not reach equally far for both jobs, and that difference is the substance of
this decision. The pipeline is already at-least-once (ADR-0005) with idempotent sinks (ADR-0006), so
re-deriving a dropped checkpoint from the source is the abnormal condition those decisions already cover.
This ADR states how far that coverage carries, and where it stops.

## Decision drivers

- **A migrated checkpoint has no correctness guarantee; a dropped one does.** Reading an incompatible
  state store risks *silent* corruption — the worst outcome, because nothing observes it. A fresh start's
  cost is bounded and knowable. Between an unknown risk and a bounded one, take the bounded one.
- **The two jobs are not symmetric under a reset**, because one is stateless and re-reads from the start
  of the stream while the other is stateful and resumes at the tail. A single "drop the checkpoint"
  sentence hides a real difference in what is lost, so the difference is decided, not left implicit.
- **The stateless job's "lossless" claim rests on Kafka retention** — a bound no prior ADR names.
- **This upgrade is not the whole-stack gate's job.** That gate exists to catch *"old state kills the new
  version"*; here the policy *drops* the old state on purpose, so the gate would test a failure mode we
  have chosen to bypass — and the Spark JVMs are outside its scope anyway (ADR-0026).

## Decision

1. **Across a Spark major, the streaming checkpoints are dropped and the streams re-derived, not
   migrated** — unless Spark's own release notes guarantee state-format compatibility for the specific
   boundary. The 3.5 → 4.1 boundary carries no such guarantee, so it takes the drop. We do not read the
   old state store under the new version: an incompatible read can be silently wrong, and the cost of
   starting fresh is bounded by the two decisions below.

2. **The reset is lossless for the stateless job and bounded-lossy for the stateful one — and this
   asymmetry is the decision, not an implementation detail.** The measurements job holds no state and
   re-reads the stream from its beginning, so an idempotent re-upsert reproduces it exactly (ADR-0006
   dec 3): no duplicates, no loss, only reprocessing time. The aggregations job holds windowed state and
   resumes at the stream's tail, so windows already closed are safe but windows still open at the cutover
   instant live only in the dropped state store and are lost or emitted partial. The correctness argument
   for the drop is therefore *different* for each job, and both halves are on record because neither is
   derivable without knowing the job's shape.

3. **The stateless job's losslessness is bounded by Kafka retention, named here as a precondition.**
   "Re-reads the stream from its beginning" means "as far back as Kafka still holds." Anything older is
   not re-derivable from Kafka — it is, separately, already durable in `sensor_measurements` and the cold
   layer (ADR-0025), which the drop never touches. The bound is a precondition of the upgrade, not an
   assumption to discover afterward.

4. **The stateful job's boundary-window loss is accepted, not engineered away.** It is bounded to the few
   windows spanning the cutover moment (at most one of each configured width per sensor), one-time, and
   self-healing as the next complete windows close. Eliminating it — dual-running two Spark versions, or
   draining ingestion to zero before cutover — costs far more than the handful of windows is worth. An
   operator who cannot accept even that bounded gap has the drain option; the platform does not require
   it.

5. **This upgrade is proven by the per-image smoke and a one-time box run, not by the whole-stack gate.**
   The per-image smoke (ADR-0026 dec 5) confirms the image *is* what the migration claims — Spark 4.1 on
   Scala 2.13 with the object-store checkpoint's S3A stack baked in — the part a dependency resolve cannot
   see. That the jobs then resolve their runtime connector, start against an empty checkpoint, and process
   a batch — the state a cutover leaves them in — and that a 3.5 checkpoint does *not* resume under 4.1,
   is empirical work for the box run (ADR-0026 dec 6), whose finding is the evidence for decision 1.
   Neither is the whole-stack gate's job: it excludes the Spark JVMs on cost, and its failure class is one
   this policy sidesteps by dropping the state. The cutover itself is a deliberate, operator-run act — not
   automatic, not zero-downtime — and its procedure is a runbook in
   `docs/architecture/stream-processing.md`; this ADR fixes only that the drop is deliberate and its cost
   is the one decided above.

## Alternatives considered

**A. Migrate the 3.5 state store into 4.1.** *Rejected.* Spark gives no cross-major state-format guarantee
for this boundary, so a "successful" read could be silently wrong — nothing observes it. A bounded,
visible fresh start beats an unbounded, invisible corruption.

**B. Dual-write — run 3.5 and 4.1 in parallel until the new one catches up.** *Rejected.* It exists to
avoid decision 4's boundary-window loss, but that loss is already bounded and self-healing, while
dual-running two Spark clusters against one Kafka topic and one hypertable doubles the resource budget
(ADR-0006's core caps) and adds a reconciliation problem. The cost dwarfs the harm it prevents.

**C. Drain ingestion to zero before cutover, so no window is open.** *Not adopted as a requirement.* It
makes decision 4's loss zero, at the price of an ingestion pause. It is a legitimate operator choice, so
the runbook offers it — but it is not required, because the default loss is already within the accepted
bound.

## Consequences

### Positive

- The upgrade has a bounded, correctness-preserving cutover with a *named* cost, instead of an implicit
  "hope the checkpoint reads." The measurements stream is provably lossless within retention; the
  aggregation loss is quantified rather than discovered.
- ADR-0006's open Consequence — "a lost checkpoint is its own recovery concern" — is answered for the one
  way a checkpoint is *deliberately* lost.
- The proof is placed where it tests the real failure mode (fresh-start smoke + box run) rather than on a
  gate whose failure mode this policy sidesteps.

### Negative

- **A handful of aggregation windows are lost at every Spark major.** Downstream, ADR-0023's feature seam
  (`sensor_aggregations_*`) and the drift monitoring that reads it (ADR-0021) see a gap for exactly those
  window slots — bounded, resolving on the next complete window. An operator who cannot accept it uses
  Alternative C.
- **The cutover is manual and maintenance-windowed** — not a zero-downtime rolling upgrade. Acceptable for
  a monitoring/aggregation workload; a workload that could not tolerate it would reopen this ADR.
- **The policy assumes Spark keeps *not* guaranteeing cross-major state compatibility.** If a future major
  ships a supported state migration, decision 1's "unless guaranteed" clause takes it; the drop is the
  fallback, not a preference.

### Neutral

- **The cold layer (ADR-0025) is unaffected** — it reads `sensor_measurements`, which the measurements job
  re-upserts idempotently and which the drop never touches. Stated so it is not re-litigated.

## Deferred decisions

- **Automating the cutover.** It is a runbook today; whether it becomes a scripted job is deferred until
  the cadence of Spark majors makes the manual run a burden.
- **A state format that survives majors** (an external state store, or a documented state migration). Only
  worth its complexity if boundary-window loss ever stops being acceptable.

## References

- ADR-0005 — at-least-once delivery; the guarantee decision 2's lossless half rests on.
- ADR-0006 — idempotent sinks, bounded state, durable checkpoints; the parent whose open "lost checkpoint"
  consequence this ADR closes for the deliberate-loss case.
- ADR-0023 — the aggregate tables as the feature seam; the named downstream of decision 4's loss.
- ADR-0025 — the cold layer; unaffected, and stated so.
- ADR-0026 — the release gates; decisions 5 (per-image smoke) and 6 (box run) are where this upgrade is
  proven, and the compose-smoke scope is why it is not proven there.
- Issue #243 — the Spark 3.5 → 4.1 migration this ADR gates; the box run confirming the incompatibility
  premise is recorded against it. Cutover procedure: `docs/architecture/stream-processing.md`.
