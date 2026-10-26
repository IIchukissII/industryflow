<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0027 — The model artifact declares; the serving environment satisfies or refuses

- **Status:** Accepted
- **Date:** 2026-07-14; changed 2026-10-26
- **Parent:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (its deferred supply chain, for the artifact path only)
- **Related:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (in-process registry), [ADR-0028](ADR-0028-model-adapter-contract-and-score-semantics.md) (model-adapter contract), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (the test is the gate), [ADR-0022](ADR-0022-concept-drift-operator-feedback.md) (retrain lane), [ADR-0030](ADR-0030-externally-authored-model-admission.md) (provenance), [ADR-0037](ADR-0037-no-author-code-on-the-serving-side.md) (no load runs author code)

## Context

A model trained in the authoring kernel is executed in the serving service, and one reconstructed
across a library boundary can fail or score wrong; the artifact records requirements the framework
does not enforce. Models cross in typed formats whose serializers are part of the boundary, and an
artifact can carry its own list of types to trust. The framework set is open; the serving image is one
pinned stack.

## Decision

1. **The artifact declares its requirements; the serving environment satisfies them or refuses it.**
   The rule is generic and needs no amendment for a new framework.
2. **What the serving environment can satisfy is a declared, closed supported flavor set.** It is the
   in-process flavors plus installed model adapters; a model outside it is refused with a reason.
3. **For in-process flavors the rule instantiates as version constraints in each library's own terms.**
   numpy major, scikit-learn major.minor and pandas major (build time only) must match; serializers on
   the serving side are at or above the kernel's, within the major.
4. **The open framework set is served through ADR-0028's model-adapter contract.**
5. **Two checks enforce the contract, and the empirical one is the authority.** A declarative check
   compares the pins each image actually installs; a required round trip trains a model in the real
   authoring image that must load and score in the real serving image.
6. **The serving service refuses an artifact it cannot serve at registration and again at deployment,
   not in the inference path.** The registration verdict expires when the serving image is rebuilt, and
   the two refusals are distinguishable.
7. **Compatibility is a property of a registered model, surfaced where models are looked at.** It is
   satisfied, satisfied with differing exact versions (warning shown), or not satisfied (no deploy).
8. **That property is never routed into the retrain recommendation (ADR-0022 dec 4).**
9. **The serving service keeps no dependency manifest that no image installs.**
10. **Models registered before this contract are served and marked, not retroactively refused.** Where
    the contract fails they cannot be re-deployed, and the remedy is a human retraining.
11. **ADR-0011's supply-chain deferral is resolved for the artifact path only.** Package installs in a
    running environment, other base-image contents and notebook promotion stay with ADR-0011.
12. **The analytics kernel is not bound by this contract.** It trains and registers nothing.
13. **Trust in what an artifact deserialises into is granted by the serving side, by provenance, never
    taken from the artifact.** Kernel-authored artifacts get a minimal tree-type list amended only here,
    uploads get none, and the serving side performs the typed load itself.

## Alternatives

| Dec | Rejected alternative | Why rejected |
|---|---|---|
| 2 | Every framework in the serving image | A multi-gigabyte layer for every self-hoster (ADR-0001) and a second numeric stack to keep in parity |
| 3 | Identical versions on both ends | Every serving patch becomes a notebook release; fails on an open framework set |
| 3 | The round trip alone, no version rule | A test cannot refuse one artifact at a gate after a later rebuild |
| 3 | A version-checked serializer in place of version constraints | It is already the format scikit-learn models cross in, and it does not make two numeric stacks agree |
| 4 | Recreate each model's environment at load | Per model is the wrong unit; per flavor is decision 4 |
| 8 | Reuse the retrain recommendation | Corrupts the statistical signal ADR-0022 makes trustworthy |
| 13 | Stay on the serializer version that trusted tree structures by default | Keeps a known memory-safety flaw on the model load path, and a security patch does not wait (ADR-0026 dec 7) |
| 13 | Trust tree structures for every artifact | Erases the provenance distinction and hands the closed flaw to an author with no foothold |
| 13 | Validate tree structures before trusting them | A standing bet on a checker against a library's private internals, which move between releases |
| 13 | Honour the trust list the artifact declares | Lets the file judged decide its own trust |
| 13 | Load typed artifacts through the tracking framework's flavor | Its loader takes trust from the artifact and refuses every typed artifact while its deserialisation switch is off |

## Consequences

**Positive**
- A bump that breaks the boundary fails a check; either side moves without a coordinated release.

**Negative**
- The round trip is costly; patch drift is permitted and can still break an artifact.
- The artifact's inferred declaration omits numpy; only the declarative check covers it.
- Frameworks the image lacks are not servable.

## Deferred

| Open question | Settled by |
|---|---|
| Admitting uploaded tree-based models without trusting their object data | Its own record, when needed |
| A complete, explicit requirement set logged by the kernel | A missed library the declarative check does not cover |
| A reconciler re-checking deployed models after a rebuild | First stale verdict that matters |
| The analytics kernel's versioning policy | Its own operational record |

## References

- `scripts/check_model_env_parity.py`, `.github/workflows/model-artifact-roundtrip.yml`: decision 5.
- `services/ml_service/api/model_compat.py`, `services/ml_service/api/skops_trust.py`: decisions 6-7, 13.
