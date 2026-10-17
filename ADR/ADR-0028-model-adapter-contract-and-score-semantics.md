<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0028 — Declared score semantics and the model-adapter contract

- **Status:** Accepted
- **Date:** 2026-07-14; changed 2026-10-17
- **Parent:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (its deferred model-adapter contract)
- **Related:** [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (the gate that refuses what the environment cannot honour), [ADR-0008](ADR-0008-extension-and-plugin-interface.md) (in-process default), [ADR-0021](ADR-0021-model-drift-monitoring.md) (alerts on these scores), [ADR-0030](ADR-0030-externally-authored-model-admission.md) (uploads declare semantics)

## Context

A detector turns a model's output into a 0–1 anomaly score, and the meaning of that output cannot be
read from its value: an outlier model's `1` means normal, a classifier's `1` means anomaly, and an
autoencoder emits no verdict, its signal being reconstruction error. A wrong score feeds alerts that
operators label. The framework set is open, while the serving image is one pinned stack.

## Decision

1. **A model's output has declared semantics, and its detector declares which it implements:
   `anomaly_probability`, `outlier_score` (a score centred on the model's own decision boundary, negative
   on the outlier side), `reconstruction_error`, `direct_score` (a 0–1 score the model means).** The
   detector is chosen once at registration, recorded on the model and dispatched on by serving, which
   refuses a model with no recorded or no available detector.
2. **The platform never infers semantics from a prediction's value or dtype.** A detector that cannot
   establish its model's semantics refuses to score.
3. **The built-in detectors are split by semantics, and the label is never the signal.** The
   compatibility detector scores only through a class probability or a boundary-centred outlier score and
   refuses otherwise; the direct-score detector refuses a value that is not finite and in range, never
   clamping it.
4. **An adapter declares which artifact flavors it can score.** An artifact no adapter handles is refused
   at ADR-0027's gate.
5. **The supported set is discovered from the registry, not maintained as a list, and it is served.** An
   operator can ask what a deployment accepts before training.
6. **In-process remains the default, and autoencoders are served inside it.** A reconstructing
   scikit-learn estimator needs no new dependency, image or serving path.
7. **Frameworks the image does not carry are refused by ADR-0027's gate; the out-of-process serving
   boundary is its own record.**

## Alternatives

| Alternative | Why rejected |
|---|---|
| Keep inferring, fix the outlier branch | The next model family collides; autoencoders stay unscoreable |
| One detector per model class, by type check | One class can carry two semantics |
| Torch in the serving image | A multi-gigabyte layer for every self-hoster (ADR-0001) |
| Ship a sidecar now | The autoencoder does not need it |
| Re-select the detector at load | Admitted and scored semantics can differ |
| Read a float prediction as a score | Decision 2's inference; `direct_score` exists |

## Consequences

**Positive**
- A model whose meaning nothing here understands is refused, not scored wrong.
- Prediction drift compares a model only with a baseline of the same semantics.

**Negative**
- Thresholds tuned against inferred scores must be re-set.
- An autoencoder registered without its training-time error scale is not scoreable.
- A model offering only a raw or offset score is refused until re-registered with a declared semantics.
- Changing a model's detector means registering it again.

## Deferred

| Open question | Settled by |
|---|---|
| The out-of-process serving boundary (decision 7) | Its own record, when torch or GPU serving is required |
| Where the reconstruction-error scale lives in the schema | A schema decision; today it is supplied at registration |

## References

- `services/ml_service/api/extensions/`: the vocabulary and built-in detectors.
- `services/ml_service/api/routers/capabilities.py`: the served supported set.
- Issue #236: every outlier-model reading scored as an anomaly.
