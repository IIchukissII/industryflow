<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0021 — Model drift monitoring (the statistical alert lane)

- **Status:** Accepted
- **Date:** 2026-06-28; changed 2026-10-17
- **Parent:** [ADR-0001](ADR-0001-industryflow-framing.md)
- **Related:** [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (declared detector
  and scale), [ADR-0022](ADR-0022-concept-drift-operator-feedback.md) (labelled drift),
  [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (pluggable detectors)

## Context

A deployed model decays silently as the live data moves away from its training data. Per-reading
anomaly detection does not notice; drift is a windowed, statistical question on a longer timescale.
Sensor streams carry no ground truth in real time. The alert schema reserves a statistical lane, and
the ML service already holds a drift library and the tenant data path.

## Decision

1. **Drift is detected without labels: data drift and prediction drift.** Prediction drift compares the
   declared detector's scores on its declared scale, never raw output, scoring the trailing window as
   live inference would; a model whose detector refuses reports no prediction drift and says why.
2. **Drift surfaces through the statistical alert lane.** A rule binds a model and a threshold.
3. **The alert service owns drift detection; the ML service computes the score.** A scheduled
   evaluator beside the rules engine applies thresholds and delegates computation over an internal
   endpoint.
4. **The baseline is a reference profile supplied at registration and stored with the platform-registry
   record; the platform builds none itself.** A profile whose prediction column is not detector scores
   is refused, and a model without one reports drift unavailable rather than guessing a baseline.
5. **Drift is evaluated on a schedule over a trailing window, independent of the per-reading path.**
6. **Every evaluation is tenant-scoped.** One model, one tenant, that tenant's data and profile.

## Alternatives

| Alternative | Why rejected |
|---|---|
| Detect in the real-time stream worker | Wrong timescale |
| A standalone drift service | Detection already has a home |
| Compute inside the alert service | Duplicates the ML service's library and data path |
| Compute in the stream aggregation | Splits detection ownership; couples cadence to the stream job |
| Labelled performance drift first | Needs a label loop; ADR-0022 |
| Compare raw model outputs | Raw output means different things per family (ADR-0028) |

## Consequences

**Positive**
- Model decay becomes an alert on the surface operators watch; the models view also evaluates a model
  against its baseline on demand.

**Negative**
- A model registered without a profile, including any notebook-trained one whose author built none, is
  not monitored; a model only in the notebook registry has no baseline.
- Drift signals distribution change, not performance loss.

## Deferred

| Open question | Settled by |
|---|---|
| How the notebook or training flow emits a reference profile | First notebook-trained model needing drift, or a training service |
| Scheduled drift outcomes on the models view | The maintainer, on operator demand |
| Offloading large windows from inline computation | First window too large to compute inline |

## References

- [docs/architecture/ml-and-features.md](../docs/architecture/ml-and-features.md),
  [docs/architecture/alerting.md](../docs/architecture/alerting.md): baseline, window, schedule.
- `services/ml_service/api/drift/`, `services/alert_service/worker/rules_engine.py`.
