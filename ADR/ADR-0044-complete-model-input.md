<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0044 — A model is scored on complete, computed input or not at all

- **Status:** Accepted
- **Date:** 2026-10-31
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

1. **A reading that lacks a sensor the configuration reads is refused, naming the missing sensors.**
   A value that is absent or not a finite number is missing. The refusal is the engine's, so every
   caller meets it.
2. **A feature definition a transform cannot interpret is refused, never scored as a substitute.** It
   is a defect of the configuration, like an unknown transform type, and no reading can make it
   computable.
3. **A substitute value is permitted only where ADR-0024 permits one, and is always reported.** A
   neutralized stateful slot and a stateful slot whose aggregate is unavailable take the neutral value,
   and the response names both. ADR-0024 keeps the per-call fallback to bound a total database outage.
4. **Any other failure to compute a feature fails the score.** An unexplained error is not a value.
5. **The platform offers no partial scoring, and equipment carries no partial-batch settings.** A
   setting nothing honours tells its author a behaviour exists that does not.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | Score a missing sensor as zero | A silent substitute; the score looks ordinary and the model never saw the value |
| 1 | Treat a missing sensor as a kill-switch neutralization | The switch governs a degraded store, not an incomplete reading; it would hide caller defects as operations events |
| 1 | Check completeness at the gateway only | Leaves every other caller of the engine unguarded |
| 1 | Refuse a missing sensor as a server error | The request is what is wrong, and a server error invites a retry that cannot succeed |
| 2 | A neutral value for a definition with a missing or unknown parameter | The same silent substitute, served on every reading until someone reads the logs |
| 3 | Narrow substitution to the kill-switch alone | Contradicts ADR-0024's outage fallback |
| 3 | A failed aggregate read substituted without being reported | Breaks ADR-0024 dec 5: degraded serving must be visible |
| 4 | Zero for any feature whose computation raises | The defect class this record removes |
| 5 | Build partial scoring on a minimum sensor count | No decision intends it, and every alert would rest on vectors the model was not trained on |
| 5 | Keep the unread settings for later | ADR-0000 dec 1: a behaviour is decided before it is offered |

## Consequences

**Positive**
- Every score is computed from the bound configuration's full input, or reports which slots are not.
- A configuration defect surfaces on its first use, not as drift in alert quality.

**Negative**
- A caller that sent incomplete readings, and was scored, is now refused.
- A model bound to a defective configuration stops scoring until it is rebound.
- Equipment created with partial-batch settings loses them; they had no effect.

## Deferred

| Open question | Settled by |
|---|---|
| Partial scoring by a model trained for missing inputs | The first model that declares it handles a missing sensor |

## References

- [docs/operations/monitoring.md](../docs/operations/monitoring.md): the degraded-serving metrics.
- `services/ml_service/api/feature_engineering/engine.py` (decisions 1–4),
  `services/ml_service/api/extensions/builtins.py` (2, 3).
