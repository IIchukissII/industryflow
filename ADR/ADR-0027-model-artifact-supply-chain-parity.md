<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0027 — Train/serve parity on the model artifact path

- **ID:** ADR-0027
- **Status:** Accepted
- **Date:** 2026-07-14
- **Project:** IndustryFlow
- **Parent:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (whose deferred *"notebook and package supply chain"* this resolves, for the artifact path and no further)
- **Companions:** [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (the gateway the artifact crosses), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (whose gate discipline decisions 4-6 apply to a second class of failure)
- **Related:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (the in-process registry that loads the artifact at the far end), [ADR-0021](ADR-0021-model-drift-monitoring.md) / [ADR-0022](ADR-0022-concept-drift-operator-feedback.md) (the alert lanes decision 7 declines to reuse), [ADR-0020](ADR-0020-notebook-per-user-persistence.md) (why a kernel stack bump reaches every user)

## Context and problem

A model authored in a notebook is trained in one environment and executed in another. Every hop of
that journey is already decided:

```
authoring kernel  ──sklearn pickle──▶  tracking gateway ──▶ registry ──▶  ml_service  ──▶  inference
   (ADR-0011)                             (ADR-0019)                     (ADR-0010 dec 6)   (ADR-0021)
```

The tenant boundary is decided. The capability that carries the artifact across it is decided. The
in-process registry that loads it at the far end is decided. **The one thing no ADR decides is
whether the pickle is still readable when it gets there** — and it is a pickle: the kernel calls
`mlflow.sklearn.log_model`, `ml_service` calls `mlflow.pyfunc.load_model`, which unpickles into
whatever environment `ml_service` happens to be running. MLflow *records* the training requirements
in the run; nothing *enforces* them on load.

The two ends do not match, and have not for some time:

|                    | Python | numpy      | scikit-learn |
|--------------------|--------|------------|--------------|
| authoring kernel   | 3.12   | **1.26.4** | **1.5.1**    |
| `ml_service`       | 3.14   | **2.5.1**  | **1.9.0**    |

So a model trained today crosses a numpy 1→2 ABI break and four scikit-learn minor releases on its
way to being served. scikit-learn promises pickle compatibility across *no* version boundary and
warns on any mismatch it notices; numpy 1→2 changed the C-level array layout. The platform's own
blessed worked example — `getting-started.ipynb`, shipped read-only in the authoring image as *the*
demonstration of read → features → train → register — walks straight across this boundary.

**This is not a consequence of the Python 3.14 bump; it is older than it.** The gap opened when
`ml_service` took numpy 2 unaccompanied. It opened silently, because no decision existed that would
have required anyone to look. The 3.14 bump merely made it impossible to keep not looking: the
kernel's pins publish no cp314 wheels, so the kernel cannot move to 3.14 without also taking numpy 2
and scikit-learn 1.9 — which would settle this question by accident, inside a dependency PR. That is
the failure mode ADR-0000 exists to prevent, so the kernel images were **held at 3.12** and the
decision brought here first.

## Decision drivers

- **A silent wrong answer is worse than a crash.** An unpickle across a numpy major does not reliably
  fail loudly; it can succeed and score wrong. The whole point of the drift lane (ADR-0021) is to
  trust the model's outputs enough to alert on them.
- **The gap was opened by a bump that no rule bound.** Whatever is decided must *bind the next bump*,
  or it decides only today and is re-lost the same way.
- **Version equality is a proxy; loading the artifact is the fact.** ADR-0026 has just paid for this
  lesson twice — a clean resolve proves nothing about a run. A version check is a description of the
  artifact; only loading it is evidence.
- **The two environments have honestly different clocks.** `ml_service` should be free to take a
  security patch without a coordinated notebook release, and the kernel is a user-facing environment
  that reaches every data scientist at their next spawn (ADR-0020 dec 1). Lockstep on every pin would
  be a real, recurring tax.
- **There is no training service.** ADR-0022 dec 4 is explicit: a model gets a new version only when a
  human trains one in a notebook. Any rule that implies "just retrain the old models" is writing a
  cheque the architecture cannot cash.

## Decision

### The constraint

1. **The model artifact path has a declared compatibility contract, and it is expressed in each
   library's own terms — not in semver's.** Between the **authoring kernel** and **`ml_service`**:

   - **numpy: the major version must match.** 1→2 is an ABI break; that is a genuine major-version
     story and major-match is the right unit.
   - **scikit-learn: `major.minor` must match.** Semver-major is *meaningless* for scikit-learn: it
     has been on major 1 since 2021, so 1.5 and 1.9 — the very drift this ADR closes — are the same
     major. scikit-learn's own contract is stricter than semver's: it guarantees pickle compatibility
     across no boundary at all, and raises `InconsistentVersionWarning` on any difference. `major.minor`
     is where its serialisation surface actually moves; patch drift within a minor is accepted residual
     risk, surfaced (decision 6) rather than blocked.

   **This constraint is a floor on divergence, not a pin.** Neither side is pinned to the other; each
   may move freely *within* a matching numpy major and scikit-learn minor.

2. **pandas major must match, as a build-time constraint only.** DataFrames cross the `predict()` call,
   not the artifact. It is bound by the declarative check (decision 4); it is *not* grounds for refusing
   a model (decision 5), because no pandas object is inside the pickle.

3. **scipy, xgboost and the rest are out of scope, and one of them is a hole this ADR names but does
   not fill.** scipy is not on the deserialisation path. xgboost is a different matter: it is in the
   authoring kernel and **not in `ml_service` at all**, so an xgboost model cannot be served today by
   any version. That is a real gap, it is *not* a parity gap, and it is recorded as deferred rather
   than smuggled into this record.

### What binds the next bump

4. **The contract is enforced by two checks, and the cheap one is not the authoritative one.**

   - A **declarative check** compares what the two environments *say* they are, and fails a change that
     breaks decision 1's contract. It is cheap, it runs on every change to either side, and it is the
     check whose absence let this gap open. **It is not the authority** — it reasons about version
     numbers, and version numbers are a proxy.
   - An **empirical check** is the authority: a model trained by the *real* authoring image must load,
     and score, in the *real* serving image. Per ADR-0026 — *the test is the gate* — the binding proof
     is the artifact making the journey, not two strings matching. It catches what version arithmetic
     cannot predict in either direction: a break inside a permitted range, or an alarm about a range
     that is in fact fine.

   Both are required on any change to either environment. A version rule without the empirical check is
   exactly the "resolves cleanly, dies on start" class ADR-0026 was written about, one layer up.

### What is served, and what the operator sees

5. **`ml_service` refuses an artifact it cannot honestly serve, and refuses it at the gates that admit a
   model — not in the inference path.** There are two such gates and they are not the same moment:
   **registration**, the earliest signal, while the author is still present and the run is fresh; and
   **deployment**, the last moment before anything is served. Both are required, because a model may sit
   unpromoted while the serving environment is rebuilt underneath it: **the registration verdict expires,
   and only the deployment gate is positioned to know it.** The two refusals are distinguishable, because
   they mean different things — one says the artifact was never servable, the other says the ground moved.

   `predict` is not where this is discovered. Re-checking per request buys nothing decision 4 and these
   two gates have not already established.

6. **Compatibility is a property of a model, surfaced where models are looked at — not an alert.** A
   registered model records whether its training environment still satisfies the contract, in three
   states: it holds; it holds but the exact versions differ (scikit-learn will warn on load, and that
   warning is shown rather than swallowed); or it is broken, and the model will not deploy.

7. **That property does not fire into `retrain_recommended`, and this is the decision, not an
   implementation detail.** ADR-0022 dec 4 gives that condition a precise meaning — *sustained drift*
   **and** *label-derived precision decay* — a statistical claim about the world, which an operator
   labels true or false from what they saw happen. A version mismatch is a mechanical fact about two
   containers: different cause, different remedy (rebuild or re-register — the model's *weights may be
   perfectly fine*), different triage path. Routing it into the statistical lane would put an
   unlabellable alert in front of the operator whose job is to label them, and would corrode the one
   signal ADR-0022 was built to make trustworthy.

8. **Models registered before this ADR are served, marked, and not retroactively refused.** They are
   already being unpickled across the gap — that is the status quo this record exists to end, and it is
   ended *forward*. Where the contract does not hold they carry the broken state of decision 6, which
   shows to the operator and bars a re-deploy; they are not torn out of the serving path they already
   occupy, and they are not auto-retrained — ADR-0022 dec 4 means there is nothing to auto-retrain them
   *with*. The remedy is a human retraining in a kernel that now matches.

### What follows

9. **ADR-0011's "notebook and package supply chain" deferral is resolved for the artifact path, and
   only for it.** Decided here: the versions of the libraries whose objects cross the pickle boundary.
   Still deferred, and still ADR-0011's: whether data scientists may `pip install` inside a running
   environment, what else the base image carries, and how curated notebooks are promoted.

10. **The analytics kernel is not bound by this contract.** It trains nothing and registers nothing; it
    is not on the artifact path. That its pins match the authoring kernel's today is a coincidence, not
    a contract, and coupling it here would charge a coordination cost for no safety. It moves on its own
    cadence — including to Python 3.14 independently.

## Alternatives considered

**A. Strict parity — pin the kernel and `ml_service` to identical versions.** *Rejected.* It is the
obvious answer and it over-buys. It makes every security patch on the serving side a coordinated,
user-visible notebook image release, and it does so to defend against patch-level drift that
scikit-learn's own release practice makes very unlikely to break a pickle. Worse, it would have "solved"
this incident by making one of the two sides wait indefinitely on the other — trading a silent gap for a
loud stall. Decision 1's floor gives the safety without the lockstep.

**B. No version rule at all — let the round-trip test be the sole authority.** *Rejected, though it is
the most intellectually honest option and half of it is adopted.* The test *is* the authority (decision
5). But a test cannot refuse a registration at request time, and it only runs when CI runs: a serving
image rebuilt after a model was registered is not covered by any test that ran in the past. Decision 6
needs a rule it can evaluate against a single artifact, at a gate, in the present tense. The rule and the
test answer different questions; keeping only one leaves the other unanswered.

**C. Per-model environments — recreate the recorded training env at load time.** *Rejected.* It is what
MLflow's `python_env.yaml` exists to enable, and it would make the whole problem go away by construction.
But ADR-0010 dec 6 puts the detector in-process on the inference path, and ADR-0021 serves models from a
warm shared cache. Per-model virtualenvs replace a warm in-process load with a multi-second cold
environment build per model version, and hand the service a package-management problem at runtime. It is
architecturally incompatible with the inference posture this platform has already chosen. If that posture
ever changes — a model-server sidecar, say — this becomes the right answer, and it is recorded as deferred
rather than dismissed.

**D. Reuse `retrain_recommended` for version incompatibility.** *Rejected* — see decision 7. It is the
cheapest thing to build (the lane exists, the UI exists) and it would quietly ruin the meaning of the one
alert ADR-0022 was written to make trustworthy.

**E. Switch serialisation to `skops` instead of constraining versions.** *Deferred, not rejected* — and
noted because the authoring image **already ships `skops`, unused**. It is a version-checked, non-pickle
sklearn format, and it addresses a real problem this ADR does not: `mlflow.pyfunc.load_model` executes
arbitrary code from the artifact, so the serving path currently trusts whatever a notebook pickled. That
is a *trust* decision, not a *parity* decision, and it needs the same round-trip gate this ADR builds
before it could be adopted safely. It gets its own record. The dependency stays in the image — it is the
foothold for that record, not dead weight.

## Consequences

### Positive

- The boundary is named, and the next bump that would break it fails a check instead of shipping.
- The proof is a real artifact making the real journey, not an assertion that two strings are equal.
- `ml_service` can still take a scikit-learn patch without a notebook release; the kernel can still add a
  library without asking the serving team.
- The kernel's Python 3.14 / numpy 2 bump — held at 3.12 for exactly this reason — is unblocked, and the
  last two images of the docker-images group with it.
- The operator is told the truth about a model whose environment no longer matches, in the place they
  already look, without a fired alert that they cannot label.

### Negative

- **The empirical check is the most expensive thing this ADR asks for**: it stands up both environments
  and moves an artifact between them. It stays affordable only by keeping what it trains trivial — it is
  testing the *boundary*, not the model.
- **The scikit-learn rule is a judgement, not a proof.** Patch-level drift is permitted and *can* in
  principle break a pickle. Decision 6's middle state exists precisely because that residual risk is
  real; it is surfaced rather than denied.
- **The registration verdict goes stale**, which is why decision 5 pays for a second gate. Even so, a
  model already deployed when its serving environment is rebuilt is not re-checked — only a re-deployment
  is. Closing that would need a background reconciler, deferred below.
- **Pre-existing broken models keep serving.** This ADR marks them; it cannot fix them, because the
  platform has no training service to fix them with (ADR-0022 dec 4).
- **The kernel stack bump reaches every data scientist at their next spawn** (ADR-0020 dec 1). Notebooks
  written against numpy 1 behaviour may need edits. This is the cost of the environment being the image's
  and not the volume's — which is ADR-0020's decision, working as intended.

## Deferred decisions

- **Safer serialisation (`skops` over pickle).** Alternative E. The *trust* half of this boundary: the
  serving path executes arbitrary code from a notebook-produced artifact. Own record; the dependency is
  already in place for it.
- **Serving non-sklearn models.** xgboost is in the kernel and absent from `ml_service` (decision 3).
  Blocked on ADR-0010's deferred model-adapter contract.
- **A background reconciler** that re-evaluates decision 6's property for already-deployed models when the
  serving environment changes, closing the staleness noted in Consequences. If it ever warrants a proactive
  notification, that is a **new, mechanical** condition — never ADR-0022's statistical lane (decision 7).
- **Per-model serving environments.** Alternative C. Revisit only if the inference posture of ADR-0010
  dec 6 changes.
- **The rest of ADR-0011's supply chain.** In-environment `pip install`, base image contents beyond the
  artifact path, curated-notebook promotion (decision 9).
- **The analytics kernel's versioning policy.** Its own operational record, when it warrants one
  (decision 10).

## References

- ADR-0011 — the deferral decision 9 resolves; ADR-0000 — the discipline that forbade settling this
  inside a dependency bump.
- ADR-0019 — the gateway the artifact crosses; ADR-0010 dec 6 / ADR-0021 — what loads it, and why per-model
  environments (Alternative C) are refused.
- ADR-0022 dec 4 — the alert lane decision 7 declines to reuse, and the "no training service" fact
  decision 8 rests on.
- ADR-0026 — the gate discipline decision 4 inherits: the test is the gate.
- Implementation: the two checks of decision 4 live in CI; the refusals and the model property of
  decisions 5-7 live in `services/ml_service`. Which files, which status codes, which column and which
  states it takes are the implementation's to choose — **this ADR fixes the properties they must
  establish, not the commands by which they establish them.**
- Issue #220 — the record of the gap; the kernel Dockerfiles carry the hold that led here.
