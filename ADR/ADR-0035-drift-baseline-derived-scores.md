<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0035 — Drift baseline: a supplied feature sample, scores derived by the platform

- **Status:** Accepted
- **Date:** 2026-10-25; changed 2026-10-28
- **Parent:** [ADR-0021](ADR-0021-model-drift-monitoring.md)
- **Related:** [ADR-0021](ADR-0021-model-drift-monitoring.md) (dec 4: the baseline decision this
  replaces), [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (the declared detector that
  scores), [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (dec 6: the deployment gate),
  [ADR-0030](ADR-0030-externally-authored-model-admission.md) (dec 7: uploads scored at that gate; dec 8:
  a declared baseline or visibly un-monitorable), [ADR-0040](ADR-0040-notebook-model-promotion.md)
  (dec 4: how a notebook supplies its sample)

## Context

Prediction drift compares the declared detector's scores on the live window with the same scores on a
baseline (ADR-0021 dec 1). The baseline has two halves of different origin. Only whoever trained the
model knows what it learned from, so the feature sample can come only from them. The score half must be
what the live side computes, but when it is supplied with the sample the platform can check only that it
is a finite number on the declared scale; a number in range can still be another quantity, computed by
another code path or feature pipeline, and prediction drift would then fire on a model that has not
moved. An externally authored model is scored off the platform by construction, and the upload flow
offers no way to supply a baseline, so uploads go unmonitored in practice. The deployment gate is the last check
before a model serves; it loads and scores an uploaded artifact in the serving image (ADR-0030 dec 7) and
judges any other model declaratively, without loading it (ADR-0027 dec 6).

## Decision

1. **A drift baseline is a feature sample the model's supplier provides and prediction scores the
   platform derives.** The supplier holds what the model learned from; only the platform can guarantee
   that both sides of the comparison come from the declared detector.
2. **The platform derives the scores by scoring the sample through the model's declared detector in the
   serving environment, at the deployment gate, for every model whatever its provenance.** It is the last
   check before serving, and the one that already loads an uploaded artifact there; for any other model
   the derivation is the gate's own load. A score column supplied with the sample is not accepted.
3. **The scores are derived again at every deployment.** The gate's verdict expires with the serving
   image (ADR-0027 dec 6), and so does a score computed by the image it replaced.
4. **A sample is accepted only when its columns are the inputs of the model's bound feature
   configuration, checked at registration; otherwise the model has no baseline and says so.** The model
   is still registered: a missing baseline makes it visibly un-monitorable (ADR-0030 dec 8), never
   refused and never given a guessed baseline.
5. **A detector that cannot score the sample leaves the model without a prediction baseline, and drift
   says why.** Data drift still runs on the sample; an autoencoder still needs its declared error scale
   (ADR-0028) before its scores mean anything.
6. **Every registration path accepts a sample, uploads included.** The supplier, not the path, decides
   whether the model is monitored.
7. **A baseline recorded before this decision keeps its supplied scores until the model is next
   deployed, and is marked as supplied until then.** Its stored sample is enough to derive scores at that
   deployment; nothing is refused retroactively (ADR-0027 dec 10).

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The supplier provides the score column with the sample | The platform can check only a range, which cannot tell the declared detector's scores from another quantity on the same scale |
| 1 | The platform capturing the reference profile itself at training time | The platform has no training flow of its own; whoever trains the model supplies its sample |
| 1 | Deriving the feature sample from stored history when none is supplied | Stored history may be compacted and is not what the model learned from |
| 1 | Raw training rows as the sample | Unbounded in size for a baseline that a sample represents |
| 1 | A bare histogram as the sample | The drift library cannot compare against it directly |
| 2 | Deriving scores only for uploaded models | A kernel-trained model's supplied scores carry the same unverifiable gap |
| 2 | Deriving scores at registration | The registration verdict expires with the serving image; the deployment gate is the last check before serving |
| 2 | Deriving scores in a separate scoring step beside the gate | A second place where a model's readiness to serve is decided, and for an upload a second load of the same artifact |
| 3 | Deriving once and keeping the scores across serving-image rebuilds | The live side would score with the new image and the baseline with the old one |
| 4 | Refusing the model when its sample does not match its feature configuration | A baseline is monitoring, not admission; ADR-0030 dec 8 already names the un-monitorable state |
| 4 | Matching sample columns to the feature configuration only at deployment | The supplier would learn of a wrong sample only when deploying |
| 7 | Discarding supplied scores at once | Every monitored model would go dark until redeployed |

## Consequences

**Positive**
- Both sides of prediction drift come from the same detector on the same serving stack.
- An uploaded model can be monitored on the same terms as any other.
- An existing baseline becomes a derived one at its model's next deployment, without its supplier.

**Negative**
- Deployment takes longer by scoring the sample, and a model that is not an upload is now loaded at the
  gate, which it was not before.
- A sample that matches an old feature configuration stops being a baseline when the binding changes.
- A model whose detector cannot score the sample is monitored for data drift only.

## Deferred

| Open question | Settled by |
|---|---|
| Whether a drift signal carries its score semantics | ADR-0028's semantics vocabulary, when drift compares across models |
| A size bound on the sample | First sample too large to score at the gate |

## References

- `services/ml_service/api/drift/reference_profile.py`: the profile format.
- `services/ml_service/api/load_probe.py`: the deployment gate's isolated load-and-score.
- [docs/architecture/ml-and-features.md](../docs/architecture/ml-and-features.md#drift-post-apidriftevaluate): baseline and evaluation.
