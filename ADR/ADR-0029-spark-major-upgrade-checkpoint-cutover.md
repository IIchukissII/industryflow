<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0029 — A Spark major upgrade resumes the checkpoint when it can and resets it when it can't; the sinks make both safe

- **ID:** ADR-0029
- **Status:** Accepted
- **Date:** 2026-07-15
- **Project:** IndustryFlow
- **Parent:** [ADR-0006](ADR-0006-spark-windowing-and-idempotent-writes.md) (bounded state, idempotent writes, durable checkpoints — the guarantee this upgrade rests on)
- **Companions:** [ADR-0005](ADR-0005-kafka-delivery-semantics.md) (at-least-once, which makes re-reading the source safe), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (where this upgrade is proven — the box run whose finding grounds this decision)
- **Related:** [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (the aggregate tables that see a bounded gap *only if* a boundary forces a reset), [ADR-0025](ADR-0025-cold-layer-historical-data-open-columnar.md) (the cold layer, which a reset never touches)

## Context and problem

Spark 4 is a major version, and structured-streaming state — the offset logs, commit logs, and the
windowed *state store* the aggregation job keeps — is a private format Spark does not *promise* is
portable across a major. So a major upgrade raises a data-correctness question before any
Scala/JDK/JAR work matters: can the new version resume the old checkpoint, and if not, what happens
to the state that was in it?

ADR-0006 stopped one step short of here. It made the sinks idempotent, bounded the state with a
watermark, and put the checkpoint on durable storage so *a restart resumes from committed offsets and
state* — then named the hole in its own Consequences: *"a corrupted or lost checkpoint becomes its
own recovery concern."* A major-version upgrade is that concern arriving on purpose, and ADR-0006 left
unrecorded what recovery *is*.

The reflex is to treat "no guarantee" as "assume it breaks" and drop the checkpoint on every major.
That is wrong twice. First, it is not what happens: on the boundary this migration actually crossed
(3.5 → 4.1), the checkpoint **resumes** — Spark reads the old offset log and reads the old state store
under its legacy encoding (box run below). Assuming failure would have thrown away correct, in-flight
window state for nothing. Second — and this is the load-bearing point — **whether it resumes or not is
not the thing the upgrade's safety depends on.** ADR-0006's idempotent sinks already make re-deriving
from the source safe. The checkpoint is an optimization that lets a restart skip re-reading; it is not
the system's source of truth. So the decision is not "migrate the state or lose it." It is: prefer the
cheap path (resume) when it is available, fall back to the safe path (reset and re-derive) when it is
not, and never assume which one holds — establish it.

## Decision drivers

- **Idempotent sinks (ADR-0006) mean a checkpoint reset is always safe, only sometimes costly.** That
  removes the pressure to migrate state at all costs — the expensive, unguaranteed thing.
- **"No guarantee" cuts both ways.** Spark does not promise resume works, so it cannot be *assumed*;
  but it does not promise it fails either, so it must not be *presumed* to. The only honest source of
  the answer is running it.
- **Resume, when it works, is strictly better than a reset:** no reprocessing, and — for the stateful
  job — no in-flight windows lost. Throwing it away by default is gratuitous.
- **A reset's cost is asymmetric between the two jobs**, so if a reset is ever forced, its cost has to
  be understood per job, not hand-waved.

## Decision

1. **A Spark major upgrade rests on the idempotent sinks, not on checkpoint portability.** Because
   every write is idempotent on its natural key (ADR-0006 dec 3) and the pipeline is at-least-once
   (ADR-0005), re-deriving a stream from the source is always safe. The checkpoint is therefore
   *disposable*, not load-bearing: the upgrade is correct whether the checkpoint is resumed or reset.

2. **The checkpoint is resumed when the new version can read it, and this is established empirically
   per boundary — never assumed.** Spark's lack of a cross-major guarantee forbids *assuming* resume;
   it equally forbids *assuming* failure. The boundary is run (the box run, ADR-0026 dec 6) and the
   observed behaviour decides. For **3.5 → 4.1 the checkpoint resumes** — offsets and state both — so
   that upgrade is a rolling restart against the existing checkpoint, with no loss.

3. **A reset is the fallback for a boundary that does not resume, and its cost is asymmetric and
   bounded.** The stateless measurements job re-reads from the beginning of the stream and re-upserts
   idempotently — lossless within Kafka retention (a precondition to check, since anything older than
   the earliest retained offset is not re-derivable from Kafka; it remains in `sensor_measurements` and
   the cold layer, ADR-0025, which a reset never touches). The stateful aggregations job resumes at the
   stream's tail, so windows already closed are safe and only the windows *open at the reset instant*
   are lost or emitted partial — bounded to a handful, one-time, self-healing as the next windows close.
   This asymmetry is decided, not left implicit, because it is the real cost of the fallback.

4. **This upgrade is proven by the per-image smoke and the box run, not the whole-stack gate.** The
   per-image smoke (ADR-0026 dec 5) confirms the image *is* what the migration claims — Spark 4.1 on
   Scala 2.13 with the object-store checkpoint's S3A stack baked in. Whether the checkpoint resumes, and
   whether the jobs process correctly under the new version, is empirical work for the box run
   (ADR-0026 dec 6) — the finding that decides decision 2 for each boundary. Neither belongs on the
   whole-stack gate: it excludes the Spark JVMs on cost, and its failure class ("old state kills the new
   version") is one this policy is built to absorb, not to be stopped by. The upgrade itself is a
   deliberate, operator-run act — the resume path is a rolling restart, the reset path adds a checkpoint
   drop — and its procedure is a runbook in `docs/architecture/stream-processing.md`; this ADR fixes only
   which path is taken and why it is safe, not the commands.

## Alternatives considered

**A. Drop the checkpoint on every major, unconditionally.** *Rejected.* It treats "no guarantee" as
"assume failure," which the 3.5 → 4.1 box run shows is false — it would have discarded resumable offsets
and live window state, taking the aggregation job's bounded-loss cost on every upgrade for no reason.
The reset is the fallback, not the default.

**B. Assume resume and skip verification.** *Rejected.* The mirror mistake. Spark gives no guarantee, so
a boundary that silently reinitialises state — or refuses to start — would be discovered in production,
not in the box run. Resume is used only where it is observed, not where it is hoped.

**C. Migrate the old state store into the new format by hand.** *Rejected.* There is no supported path,
so a "successful" migration could be silently wrong — the worst outcome. When resume is unavailable, the
reset (decision 3) is the safe answer, because the sinks make re-derivation correct; hand-migration buys
nothing the reset does not, at the price of an unbounded risk.

## Consequences

### Positive

- The upgrade is correct by construction (idempotent sinks) and cheap when the checkpoint resumes — no
  reprocessing, no window loss — as it does for 3.5 → 4.1.
- ADR-0006's open Consequence — "a lost checkpoint is its own recovery concern" — is answered: a reset
  is safe by design, and its per-job cost is named.
- The resume-vs-reset choice is grounded in an observation (the box run) rather than an assumption in
  either direction.

### Negative

- **Every major must be run before it is trusted.** The resume claim is per-boundary; it does not
  generalise from 3.5 → 4.1 to the next major. The box run is a required step of a Spark major upgrade,
  not an optional one.
- **If a future boundary forces a reset,** a handful of aggregation windows are lost, and ADR-0023's
  feature seam (`sensor_aggregations_*`) plus the drift monitoring that reads it (ADR-0021) see a gap for
  exactly those window slots — bounded, resolving on the next complete window. This cost is not incurred
  by 3.5 → 4.1, which resumes.
- **The upgrade is maintenance-windowed, not zero-downtime** — the jobs stop and restart. Acceptable for
  a monitoring/aggregation workload; a workload that could not tolerate it would reopen this ADR.

### Neutral

- **The cold layer (ADR-0025) is unaffected** either way — it reads `sensor_measurements`, which neither
  a resume nor a reset removes. Stated so it is not re-litigated.

## Deferred decisions

- **Automating the upgrade.** It is a runbook today; whether the resume/reset decision and the restart
  become a scripted job is deferred until the cadence of Spark majors makes the manual run a burden.
- **A state format that survives majors** (an external state store, or a documented migration). Only
  worth its complexity if a future boundary both fails to resume *and* the bounded reset loss stops being
  acceptable.

## References

- ADR-0005 — at-least-once delivery; the guarantee that makes re-reading the source safe.
- ADR-0006 — idempotent sinks, bounded state, durable checkpoints; the parent whose open "lost
  checkpoint" consequence this ADR closes, and whose idempotency is the load-bearing safety net.
- ADR-0023 — the aggregate tables as the feature seam; the named downstream of a *reset's* window loss.
- ADR-0025 — the cold layer; unaffected by either path, and stated so.
- ADR-0026 — the release gates; decision 5 (per-image smoke) and decision 6 (box run) are where this
  upgrade is proven, and the compose-smoke scope is why it is not proven there.
- Issue #243 — the Spark 3.5 → 4.1 migration this ADR gates. **Box run (2026-07-15, industryflow.local):**
  4.1.2 resumed the live 3.5.0 checkpoint — the measurements job resumed committed offsets (not a
  reprocess from earliest) and the aggregations job resumed the state store under the legacy `unsaferow`
  encoding; streaming 220 fresh readings landed as measurements and drove aggregation upserts. That is
  the evidence for decisions 1 and 2. Cutover procedure: `docs/architecture/stream-processing.md`.
