<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0040 — A notebook-registered model version becomes a platform model by promotion

- **Status:** Accepted
- **Date:** 2026-10-28
- **Parent:** [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (dec 9: the kernel registers
  model versions in its tenant)
- **Related:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (dec 2: the
  kernel is untrusted), [ADR-0012](ADR-0012-notebook-credential-delivery.md) (dec 1: kernel credentials),
  [ADR-0004](ADR-0004-api-authentication-sessions-and-transport.md) (dec 5: the writing role),
  [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (dec 5: the registration gate),
  [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (dec 1: the detector declares),
  [ADR-0030](ADR-0030-externally-authored-model-admission.md) (dec 8: provenance),
  [ADR-0035](ADR-0035-drift-baseline-derived-scores.md) (dec 1, 4, 6: the supplied sample),
  [ADR-0039](ADR-0039-one-tenant-ownership-test.md) (the tenant's registry names)

## Context

An authoring kernel logs runs and registers model versions in its tenant's registry. Serving, drift
monitoring and alerting act on platform models, which only the registration gate creates, and the gate
answers a user's session; a kernel holds per-session capabilities and no session. No path carries a
notebook-trained version into serving, and the feature sample a drift baseline needs has no way out of
the notebook.

## Decision

1. **A notebook-registered model version becomes a platform model only when a user with a writing role
   promotes it from the product.** Serving a model is a user's decision, taken on their session; the
   kernel never creates a platform model and gains no credential to do so.
2. **Promotion is registration, judged by the same gate.** The ML service reads the version and its run
   itself, as trusted platform code; the version must be the caller's tenant's, and its run is its
   provenance.
3. **The promoting user binds the feature configuration and chooses the detector.** They decide what the
   model's inputs are and what its output means on the platform; a kernel's assertion of either is not
   taken.
4. **The feature sample travels as a data artifact of the version's run, under a name the platform
   fixes.** The kernel logs it through its tracking capability; the ML service reads it as data, within
   a size bound, and holds it to ADR-0035 dec 4. A missing, oversized or unreadable sample leaves the
   model without a baseline, and the model says why.
5. **A version is promoted once.** The platform model is the record of what was served under that name
   and version; promoting it again is refused.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 1 | The kernel registers platform models with a registration capability | Untrusted code would take a decision the platform must trust, through a new kernel credential |
| 1 | Every registered version becomes a platform model | Serving would follow from a kernel action with no user deciding |
| 3 | The kernel records feature configuration and detector as run tags | A kernel's claim of what its output means is the assertion ADR-0028 dec 1 does not take |
| 4 | The browser sends the sample with the promotion | The browser holds no copy; the run is where the supplier recorded what the model learned from |
| 4 | A sample derived from stored history at promotion | Not what the model learned from (ADR-0035 dec 1) |
| 5 | Promoting again replaces the platform model | A model version is a record of what was served |

## Consequences

**Positive**
- Notebook-trained models reach serving and drift monitoring without a new kernel credential.
- Every platform model passes one registration gate, whatever path brought it.

**Negative**
- Each version is promoted by hand.
- A notebook that logs no sample produces models without a drift baseline.

## Deferred

| Open question | Settled by |
|---|---|
| Promotion by a training service without a user session | The first training service |

## References

- [docs/architecture/ml-and-features.md](../docs/architecture/ml-and-features.md): promotion and the
  sample artifact.
- [docs/architecture/notebooks.md](../docs/architecture/notebooks.md): logging the sample from a notebook.
