<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0044 — A model is scored on complete, computed input or not at all

- **Status:** Accepted
- **Date:** 2026-10-31; changed 2026-11-08
- **Parent:** [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (one computation of a
  feature for training and serving)
- **Related:** [ADR-0024](ADR-0024-stateful-feature-kill-switch.md) (dec 3, 5: the neutral values it
  permits and their visibility), [ADR-0041](ADR-0041-bound-feature-configuration.md) (dec 2: the bound
  inputs), [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (dec 2: refusing over
  guessing), [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (dec 3: the transform contract),
  [ADR-0034](ADR-0034-platform-schema-migration-strategy.md) (the schema change)

## Context

A model is trained on a vector its bound configuration computes from named sensors. Serving accepts
readings from a caller and computes that vector; a sensor the caller omits, a transform that cannot
compute, or a configuration a transform cannot interpret each leave a slot without its value. A
substitute in that slot yields a score indistinguishable from an ordinary one, and alerts are raised
on scores. The alert path already scores only snapshots that carry every sensor. The equipment record
carries partial-batch settings that no part of the platform reads.

## Decision

1. **A reading that lacks what the configuration reads is refused, naming what is missing.** That is
   a sensor, a value that is absent or not a finite number, and, for a stateful feature, the
   equipment whose baseline it reads. The refusal is the engine's, so every caller meets it.
2. **A feature definition a transform cannot interpret is refused, never scored as a substitute.** It
   is a defect of the configuration, like an unknown transform type, and no reading can make it
   computable. A definition names every parameter it is computed with, the window of a stateful
   feature included; the platform supplies no default. Its transform checks it where the
   configuration is written and before any reading is scored, whatever the kill-switch says.
3. **A substitute value is permitted only where ADR-0024 permits one, and is always reported.** A
   neutralized stateful slot and a stateful slot whose aggregate is unavailable take the neutral value,
   and the response names both. ADR-0024 keeps the per-call fallback to bound a total database outage.
4. **Any other failure to compute a feature fails the score.** An unexplained error is not a value, and
   neither is a computed value that is not a finite number.
5. **The platform offers no partial scoring, and equipment carries no partial-batch settings.** A
   setting nothing honours tells its author a behaviour exists that does not.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Score a missing sensor as zero | A silent substitute; the score looks ordinary and the model never saw the value |
| 1 | Treat a missing sensor as a kill-switch neutralization | The switch governs a degraded store, not an incomplete reading; it would hide caller defects as operations events |
| 1 | Check completeness at the gateway only | Leaves every other caller of the engine unguarded |
| 1 | Refuse a missing sensor as a server error | The request is what is wrong, and a server error invites a retry that cannot succeed |
| 1 | A stateful feature for a reading naming no equipment served neutral, as an unavailable aggregate | Nothing is unavailable; it reports a caller defect as an operations event |
| 2 | A neutral value for a definition with a missing or unknown parameter | The same silent substitute, served on every reading until someone reads the logs |
| 2 | A platform default for a parameter a definition omits | A bound model's input would follow a constant no record names, and changing it would change every model |
| 2 | A default window declared in the shared contract | The same default, kept in another place; the window decides which aggregate a feature reads |
| 2 | Check a definition only when its transform computes | A switched-off stateful class is not computed, so its defect would wait for the switch to be turned back on |
| 3 | Narrow substitution to the kill-switch alone | Contradicts ADR-0024's outage fallback |
| 3 | A failed aggregate read substituted without being reported | Breaks ADR-0024 dec 5: degraded serving must be visible |
| 4 | Zero for any feature whose computation raises | The defect class this record removes |
| 4 | A nudged denominator so a ratio of zero stays finite | A huge number served as an ordinary feature where the quotient does not exist |
| 4 | Refuse a ratio of zero as the caller's defect | The reading is complete and valid; it is the definition that cannot compute on it |
| 5 | Build partial scoring on a minimum sensor count | No decision intends it, and every alert would rest on vectors the model was not trained on |
| 5 | Keep the unread settings for later | ADR-0000 dec 1: a behaviour is decided before it is offered |

## Consequences

**Positive**
- Every score is computed from the bound configuration's full input, or reports which slots are not.
- A configuration defect surfaces on its first use, not as drift in alert quality.

**Negative**
- A caller that sent incomplete readings, and was scored, is now refused.
- A model bound to a defective configuration stops scoring until it is registered again on a corrected one.
- A stateful feature is scored only for readings that name their equipment.
- Extensions break under ADR-0010 dec 3: a transform registers the check of its definition, and the
  baseline read it is handed takes its window explicitly and raises for one the contract does not declare.
- Equipment created with partial-batch settings loses them; they had no effect.

## Deferred

| Open question | Settled by |
|---|---|
| Partial scoring by a model trained for missing inputs | The first model that declares it handles a missing sensor |

## References

- [docs/operations/monitoring.md](../docs/operations/monitoring.md): the degraded-serving metrics.
- [ML and feature engineering](../docs/architecture/ml-and-features.md): manual of decisions 1-5.
