<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0044 — A model is scored on complete, computed input or not at all

- **Status:** Accepted
- **Date:** 2026-10-31; changed 2026-11-12
- **Parent:** [ADR-0023](ADR-0023-stream-materialized-feature-engineering.md) (one computation of a
  feature for training and serving)
- **Related:** [ADR-0024](ADR-0024-stateful-feature-kill-switch.md) (dec 3, 5: the neutral values it
  permits and their visibility), [ADR-0041](ADR-0041-a-model-s-feature-configuration-is-fixed-once-bound.md) (dec 2: the bound
  inputs), [ADR-0028](ADR-0028-declared-score-semantics-and-the-model-adapter-contract.md) (dec 2: refusing over
  guessing), [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (dec 3: the transform contract),
  [ADR-0034](ADR-0034-platform-schema-migrations.md) (the schema change),
  [ADR-0022](ADR-0022-concept-drift-operator-feedback.md) (dec 2-4: degraded alerts in labels and precision),
  [ADR-0006](ADR-0006-spark-windowing-and-idempotent-writes.md) (dec 9: every window carries its spread)

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
   equipment whose baseline it reads, which must exist with that sensor. The refusal is the engine's,
   so every caller meets it.
2. **A feature definition a transform cannot interpret is refused, never scored as a substitute.** It
   is a defect of the configuration, like an unknown transform type, and no reading can make it
   computable. A definition names every parameter it is computed with, the window of a stateful
   feature included; the platform supplies no default. Its transform checks it where the
   configuration is written and before any reading is scored, whatever the kill-switch says.
3. **A substitute value is permitted only where ADR-0024 permits one, and is always reported.** A
   neutralized stateful slot and a stateful slot whose aggregate is unavailable, because the store is
   out or the sensor has no closed window yet, take the neutral value, and the response names both. A
   statistic missing from a window that exists is a defect under decision 4. An alert raised on such a score records, as data, that it was degraded
   and which features. ADR-0024 keeps the per-call fallback to bound a total database outage. A score
   compared as a distribution, prediction drift and its baseline, takes no substitute: it is not
   computed, and says why.
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
| 1 | An equipment id that names no equipment, or a sensor it lacks, served as an unavailable aggregate | The same caller defect, under a well-formed id |
| 2 | A neutral value for a definition with a missing or unknown parameter | The same silent substitute, served on every reading until someone reads the logs |
| 2 | A platform default for a parameter a definition omits | A bound model's input would follow a constant no record names, and changing it would change every model |
| 2 | A default window declared in the shared contract | The same default, kept in another place; the window decides which aggregate a feature reads |
| 2 | Check a definition only when its transform computes | A switched-off stateful class is not computed, so its defect would wait for the switch to be turned back on |
| 3 | Narrow substitution to the kill-switch alone | Contradicts ADR-0024's outage fallback |
| 3 | A failed aggregate read substituted without being reported | Breaks ADR-0024 dec 5: degraded serving must be visible |
| 3 | A degraded alert told only in its message | Neither precision nor the retrain trigger can tell it apart, and an operator labels it as an ordinary one |
| 3 | Drift scored on substitutes and its alert recorded as degraded | A degraded drift alert still counts toward sustained drift (ADR-0022 dec 4), and a baseline derived during a switch episode stands until the next deployment |
| 3 | A statistic undefined on an existing window served as unavailable | A data condition reported as an outage, for ever on a sparse sensor, whose drift then is never computed; ADR-0006 dec 9 defines it instead |
| 3, 4 | Every failed aggregate read served as unavailable | A defect of the query or the schema would be a substitute for ever; the fallback bounds an outage, not a defect |
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
- A model with a ratio feature is not scored for a reading whose denominator is zero, such as stopped
  equipment, and a ratio is now the exact quotient where a near-zero denominator once shifted it.
- An alert raised before degradation was recorded reads as unknown; nothing infers it from its message.
- Extensions break under ADR-0010 dec 3: a transform registers the check of its definition, and the
  baseline read it is handed takes its window explicitly and raises for one the contract does not declare.
- Equipment created with partial-batch settings loses them; they had no effect.
- Extensions break again under ADR-0010 dec 3: the baseline read refuses an equipment without the named
  sensor and fails on any error but an outage, where it answered with no value, and a transform scoring
  a stored reading is handed a read bound to that reading's time.
- While the kill-switch is off or an aggregate is unavailable, prediction drift is not computed for a
  model with stateful features.

## Deferred

| Open question | Settled by |
|---|---|
| Partial scoring by a model trained for missing inputs | The first model that declares it handles a missing sensor |

## References

- [docs/operations/monitoring.md](../docs/operations/monitoring.md): the degraded-serving metrics;
  manual of decision 3.
- [ML and feature engineering](../docs/architecture/ml-and-features.md): manual of decisions 1-5.
