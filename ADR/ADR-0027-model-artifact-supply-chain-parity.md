<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0027 — The model artifact declares; the serving environment satisfies or refuses

- **ID:** ADR-0027
- **Status:** Accepted
- **Date:** 2026-07-14
- **Project:** IndustryFlow
- **Parent:** [ADR-0011](ADR-0011-embedded-notebooks-for-analytics-and-experimentation.md) (whose deferred *"notebook and package supply chain"* this resolves, for the artifact path and no further)
- **Companions:** [ADR-0019](ADR-0019-notebook-experiment-tracking-gateway.md) (the gateway the artifact crosses), [ADR-0026](ADR-0026-release-model-and-the-compose-smoke-gate.md) (whose gate discipline decision 5 applies to a second class of failure)
- **Related:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (the in-process registry that loads the artifact, and the **deferred model-adapter contract** decision 4 hands the open framework set to), [ADR-0008](ADR-0008-extension-and-plugin-interface.md) (which already names *model adapters* as a plugin category), [ADR-0021](ADR-0021-model-drift-monitoring.md) / [ADR-0022](ADR-0022-concept-drift-operator-feedback.md) (the alert lane decision 8 declines to reuse), [ADR-0020](ADR-0020-notebook-per-user-persistence.md) (why a kernel stack bump reaches every user)

## Context and problem

A model authored in a notebook is trained in one environment and executed in another. Every hop of
that journey is already decided:

```
authoring kernel  ──serialized model──▶  tracking gateway ──▶ registry ──▶  ml_service  ──▶  inference
   (ADR-0011)                               (ADR-0019)                     (ADR-0010 dec 6)   (ADR-0021)
```

The tenant boundary is decided. The capability that carries the artifact across it is decided. The
in-process registry that loads it at the far end is decided. **The one thing no ADR decides is whether
the thing still loads when it gets there.** The kernel logs a model through an MLflow flavor;
`ml_service` calls `mlflow.pyfunc.load_model`, which reconstructs it inside whatever environment
`ml_service` happens to be running. MLflow *records* the run's requirements — it writes
`requirements.txt` and `python_env.yaml` into the artifact itself — and then *enforces none of them*
on load.

**What actually crosses is not what it looks like from the outside, and getting this wrong is the
easiest mistake here** (this ADR's own first draft made it, and so did the architecture review that
fed it — both asserted "it is a pickle", and both were wrong):

- **scikit-learn models cross as `model.skops`.** MLflow 3's sklearn flavor defaults to
  `serialization_format="skops"` — a structured, version-checked format, *not* a pickle. `skops` is a
  hard dependency of `mlflow`, and the authoring kernel pins it explicitly because `mlflow-skinny`
  does **not** pull it in. It was never the unused hardening library it appeared to be; it is the
  format the artifact is *in*.
- **xgboost models cross as a native booster** (`mlflow.xgboost`, json/ubj) — not a pickled wrapper.
  Routing an `XGBRegressor` through the *sklearn* flavor does not merely work badly: skops refuses it
  outright as an untrusted type.

The two ends do not match, and have not for some time:

|                    | Python | numpy      | scikit-learn | xgboost   | skops        |
|--------------------|--------|------------|--------------|-----------|--------------|
| authoring kernel   | 3.12   | **1.26.4** | **1.5.1**    | **2.1.1** | **0.11.0**   |
| `ml_service`       | 3.14   | **2.5.1**  | **1.9.0**    | **2.0.3** | *(floating)* |

A model trained today crosses a numpy 1→2 ABI break and four scikit-learn minor releases on its way to
being served. scikit-learn guarantees compatibility across no version boundary and warns on any
difference; numpy 1→2 changed the C-level array layout, and numpy arrays are what these formats are
mostly made of. **The serializer itself was unpinned on the serving side** — MLflow bounds skops only
as `skops<1`, so the kernel wrote its artifacts with one version and the serving image read them with
whatever `pip` happened to resolve on the day it was last built. And xgboost crossed *backwards*, into
an **older** reader than the one that wrote it.

The platform's own blessed worked example — `getting-started.ipynb`, shipped read-only in the authoring
image as *the* demonstration of read → features → train → register — walks straight across this boundary.

**A live break was found while building this ADR's gate, and it was already in the serving image:**
xgboost 2.x cannot save under scikit-learn 1.9 at all. sklearn 1.9 removed `_estimator_type` in favour
of `__sklearn_tags__`; xgboost 2.x's wrapper still reaches for it, so `save_model()` — and with it the
entire `mlflow.xgboost` flavor — raises. It *fits* and it *predicts*, which is precisely why nobody
noticed: the break is on the **save** path, and nothing in CI had ever saved an xgboost model.

**This is older than the Python 3.14 bump.** The gap opened when `ml_service` took numpy 2
unaccompanied — silently, because no decision existed that would have required anyone to look. The
3.14 bump merely made it impossible to keep not looking: the kernel's pins publish no cp314 wheels,
so the kernel cannot move without also taking numpy 2 and scikit-learn 1.9, settling this question by
accident inside a dependency PR. That is the failure ADR-0000 exists to prevent, so the kernel images
were **held at 3.12** and the decision brought here first.

### The framework set is open, and that is what makes the naive answer wrong

The obvious decision is "pin the two ends to the same numpy, scikit-learn and xgboost." It is wrong,
and the reason is a product constraint, not a technical one: **xgboost is not a privileged model type.
It is one of many.** The platform must be able to serve autoencoders, and torch models, and whatever a
data scientist reaches for next. ADR-0008 dec 3 already anticipated this — it names **model adapters**
as a first-class plugin category — and ADR-0010 leaves the *model-adapter contract* explicitly
deferred.

An enumerated rule ("numpy, scikit-learn, xgboost must match") does not survive that:

- **It does not generalise.** Every framework a user picks would mean amending this ADR to add a row.
  A decision that must be edited whenever a user chooses a library is not a decision; it is a registry,
  and it will rot.
- **It quietly assumes a closed world.** `ml_service` is one image with one pinned stack, loading models
  in-process (ADR-0010 dec 6) from a warm shared cache (ADR-0021). Torch is multi-gigabyte and
  CUDA-flavoured; Keras drags a second numeric stack. **You cannot pin every framework a notebook might
  import into the one container that serves them** — and a self-hoster who never trains a torch model
  should not carry a torch layer (ADR-0001).

So the rule has to be stated one level up, and the enumerated pins recognised for what they are: the
*instantiation* of that rule for the flavors this platform serves today.

## Decision drivers

- **A silent wrong answer is worse than a crash.** Reconstructing a model across a numpy major does not
  reliably fail loudly; it can succeed and score wrong. The drift lane (ADR-0021) trusts these outputs enough to
  alert on them.
- **The gap was opened by a bump that no rule bound.** Whatever is decided must *bind the next bump*, or
  it decides only today and is re-lost the same way.
- **Version equality is a proxy; loading the artifact is the fact.** ADR-0026 paid for this lesson twice:
  a clean resolve proves nothing about a run.
- **The two environments have honestly different clocks.** `ml_service` should be free to take a security
  patch without a coordinated notebook release, and the kernel reaches every data scientist at their next
  spawn (ADR-0020 dec 1). Lockstep on every pin is a recurring tax.
- **An open framework set cannot be served by a closed image.** The honest options are *refuse it* or
  *adapt to it* — never *pretend to serve it*.
- **There is no training service.** ADR-0022 dec 4: a model gets a new version only when a human trains
  one. Any rule implying "just retrain the old models" writes a cheque the architecture cannot cash.

## Decision

### The rule

1. **The artifact declares its requirements; the serving environment satisfies them or refuses the
   model.** The authority for what a model needs is **the artifact itself** — MLflow already records the
   run's requirements, and they travel with it. The serving side's obligation is to *check that authority*
   and to refuse a model whose requirements it cannot meet, rather than to load it and hope.

   This is the whole rule, and it is deliberately generic: it needs **no amendment when torch arrives**. A
   torch model meeting a serving environment with no torch is refused — cleanly, at the gate, with a
   reason — instead of being silently reconstructed inside a stack that cannot honour it.

2. **What the serving environment can satisfy is a declared, closed *supported flavor set* — even though
   the framework set users may author in is open.** The supported set is the union of what `ml_service`
   carries in-process and what model adapters are installed (decision 4). A model outside it is not a bug
   and not a crash: it is a **refusal with a reason**, and that is the honest answer an open-core platform
   owes an operator who never installed a torch adapter.

3. **For the flavors served in-process today, the rule instantiates as version constraints — and these are
   an instance, not the rule.** They are expressed in each library's own terms, because semver is not the
   unit that matters:

   - **numpy: the major must match.** 1→2 is an ABI break.
   - **scikit-learn: `major.minor` must match.** Semver-major is *meaningless* here — scikit-learn has been
     on major 1 since 2021, so 1.5 and 1.9, the very drift this ADR closes, are the same major. Its own
     contract is stricter than semver's: no compatibility guarantee across any boundary, and it warns on
     any difference. `major.minor` is where its serialisation surface actually moves.
   - **The serializers — `skops` and `xgboost` — must satisfy: serving `>=` kernel, within the major.**
     Not equality: a *direction*. These two libraries **write the artifact's bytes**, and each versions
     its own format and reads older ones forward — but neither promises that an *older* reader can make
     sense of a *newer* writer's file. Loading runs forward: an old model into a new reader. Today's gap
     runs backwards for xgboost (kernel 2.1.1 → serving 2.0.3) and is *unpinned* for skops, and both are
     closed by moving **serving up**, never by pinning the kernel down.

     They are in this contract despite neither looking like it belongs. skops looked like an unused
     hardening library; it is the format sklearn models are stored in. xgboost looked like an ordinary
     ML dependency; its models cross as native boosters.

     **xgboost additionally carries a floor that is not about parity at all:** 3.x is *required* by
     scikit-learn 1.9 on both ends, because 2.x cannot save under it (above). A parity rule alone would
     have happily allowed 2.x on both sides — matching, and broken.
   - **pandas: the major must match**, as a build-time constraint only. DataFrames cross the `predict()`
     call, not the artifact, so it binds the declarative check (decision 5) and is *not* grounds for
     refusing a model (decision 6).

   **This is a floor on divergence, not a pin.** Neither side is pinned to the other; each may move freely
   within it. When the supported flavor set grows, this list grows with it — that is the rule working, not
   the rule being amended.

4. **The open framework set is served through the model-adapter contract — which this ADR does not define,
   and hands to its own record.** ADR-0008 dec 3 already names model adapters as a plugin category;
   ADR-0010 dec 4 already permits an out-of-process plugin *where isolation or language independence
   justifies the cost*, and already defers the adapter contract's signature. A multi-gigabyte CUDA
   framework is precisely the case that justifies that cost.

   What this ADR fixes is only the **boundary condition**: a model whose requirements no installed
   flavor can satisfy is refused at the gate (decision 1). **What it does not fix — and must not, because
   it is a larger decision than this one — is how a new flavor is added**: the adapter signature, the
   in-process-versus-sidecar policy, the packaging of optional flavor sidecars.

   That is ADR-0010's deferred model-adapter contract, and it is now **live work, not future work**: the
   product requires the open set. This ADR is the door frame; that ADR is the door. They are separate
   because they answer different questions — *what must match, and what happens when it does not?* versus
   *how do you add a new thing that can match?*

### What binds the next bump

5. **The contract is enforced by two checks, and the cheap one is not the authoritative one.**

   - A **declarative check** compares what the two environments *say* they are and fails a change that
     breaks decision 3. It is cheap, it runs on every change to either side, and it is the check whose
     absence let this gap open. **It is not the authority** — it reasons about version numbers, and
     version numbers are a proxy.

     **It reads what the image actually installs.** This is a decision, not an implementation note: a
     check pointed at a file no image installs is not a parity check — it is green and meaningless. The
     authoritative source is the requirements file on the image's own build path; anything else, however
     intuitively named, is not. (See decision 9: we know this because it is the mistake that was made.)
   - An **empirical check** is the authority: a model trained by the *real* authoring image must load,
     and score, in the *real* serving image. Per ADR-0026 — *the test is the gate* — the binding proof is
     the artifact making the journey, not two strings matching. It catches what version arithmetic cannot
     predict in either direction: a break inside a permitted range, or an alarm about a range that is in
     fact fine.

   Both are required on any change to either environment. A version rule without the empirical check is
   exactly the "resolves cleanly, dies on start" class ADR-0026 was written about, one layer up.

### What is served, and what the operator sees

6. **`ml_service` refuses an artifact it cannot honestly serve, at the gates that admit a model — not in
   the inference path.** There are two such gates and they are not the same moment: **registration**, the
   earliest signal, while the author is still present and the run is fresh; and **deployment**, the last
   moment before anything is served. Both are required, because a model may sit unpromoted while the
   serving environment is rebuilt underneath it: **the registration verdict expires, and only the
   deployment gate is positioned to know it.** The two refusals are distinguishable, because they mean
   different things — one says the artifact was never servable, the other says the ground moved.

   `predict` is not where this is discovered. Re-checking per request buys nothing decision 5 and these two
   gates have not already established.

7. **Compatibility is a property of a model, surfaced where models are looked at — not an alert.** A
   registered model records whether the serving environment still satisfies what it declares, in three
   states: it does; it does, but the exact versions differ (scikit-learn will warn on load, and that
   warning is shown rather than swallowed); or it does not, and the model will not deploy.

8. **That property does not fire into `retrain_recommended`, and this is the decision, not an
   implementation detail.** ADR-0022 dec 4 gives that condition a precise meaning — *sustained drift* **and**
   *label-derived precision decay* — a statistical claim about the world, which an operator labels true or
   false from what they saw happen. A version mismatch is a mechanical fact about two containers: different
   cause, different remedy (rebuild or re-register — the model's *weights may be perfectly fine*), different
   triage path. Routing it into the statistical lane would put an unlabellable alert in front of the
   operator whose job is to label them, and would corrode the one signal ADR-0022 was built to make
   trustworthy.

9. **`services/ml_service/requirements.txt` is removed.** It is installed by no image, referenced by no
   Dockerfile, and cited by no ADR — the serving image builds from `services/ml_service/api/requirements.txt`.
   Yet it lists numpy, scikit-learn, pandas and scipy, so it *reads* like the service's dependency manifest,
   and the repo-wide resolvability gate keeps it green forever. **It is the file that made both the
   architecture review and this ADR's first draft get xgboost wrong** — they concluded xgboost was absent
   from serving, when it is present, pinned, and dispatched by name in the detector.

   A file that passes CI while teaching every reader something false is not inert. It is deleted rather than
   annotated. If a local-dev convenience file is ever wanted back, it must say what it is not, and it must
   not be able to masquerade as the image's manifest.

10. **Models registered before this ADR are served, marked, and not retroactively refused.** They are
    already being reconstructed across the gap — that is the status quo this record ends, and it is ended
    *forward*. Where the contract does not hold they carry the broken state of decision 7, which shows to
    the operator and bars a re-deploy; they are not torn out of the serving path they already occupy, and
    they are not auto-retrained — ADR-0022 dec 4 means there is nothing to auto-retrain them *with*. The
    remedy is a human retraining in a kernel that now matches.

### What follows

11. **ADR-0011's "notebook and package supply chain" deferral is resolved for the artifact path, and only
    for it.** Decided here: what governs the libraries whose objects cross the boundary. Still deferred, and
    still ADR-0011's: whether data scientists may `pip install` inside a running environment, what else the
    base image carries, and how curated notebooks are promoted.

12. **The analytics kernel is not bound by this contract.** It trains nothing and registers nothing; it is
    not on the artifact path. That its pins match the authoring kernel's today is a coincidence, not a
    contract, and coupling it here would charge a coordination cost for no safety. It moves on its own
    cadence — including to Python 3.14 independently.

## Alternatives considered

**A. Strict parity — pin the kernel and `ml_service` to identical versions.** *Rejected.* It is the obvious
answer and it over-buys: every security patch on the serving side becomes a coordinated, user-visible
notebook release, to defend against patch drift that scikit-learn's release practice makes unlikely to break
an artifact. It would also have "solved" this incident by making one side wait indefinitely on the other —
trading a silent gap for a loud stall. And it is the shape that dies on contact with the open framework set.

**B. No version rule — let the empirical check be the sole authority.** *Rejected, though it is the most
honest option and half of it is adopted.* The empirical check *is* the authority (decision 5). But a test
cannot refuse a registration at request time, and it only runs when CI runs: a serving image rebuilt after a
model was registered is covered by no test that ran in the past. Decision 6 needs a rule it can evaluate
against one artifact, at a gate, in the present tense. The rule and the test answer different questions.

**C. Per-model serving environments — recreate each model's recorded env at load time.** *Rejected as
stated, and superseded by decision 4.* Per **model** is the wrong unit: it would replace a warm in-process
load (ADR-0010 dec 6, ADR-0021) with a cold environment build per model version and hand the service a
package manager at runtime. But the *idea underneath it* — that not everything can live in one process — is
right, and the correct unit is per **flavor**: an adapter, in-process where it is cheap, out-of-process
where it is not (ADR-0010 dec 4 already permits exactly this). The sklearn hot path is untouched; the open
set arrives through adapters. That is decision 4, and its contract is its own record.

**D. Install every framework into the serving image.** *Rejected.* This is what "support torch" naively
means, and it is not support: it is a multi-gigabyte CUDA layer charged to every self-hoster who never
trains a torch model (ADR-0001), a second numeric stack to keep in parity with the first, and a dependency
resolution that gets harder with each framework added. An open set cannot be served by enumeration.

**E. Reuse `retrain_recommended` for version incompatibility.** *Rejected* — see decision 8. It is the
cheapest thing to build (the lane exists, the UI exists) and it would quietly ruin the meaning of the one
alert ADR-0022 was written to make trustworthy.

**F. "Adopt `skops` instead of constraining versions."** *Moot — it is already adopted, and this ADR's
first draft did not know it.* The proposal was to move sklearn artifacts off pickle onto a version-checked
format. MLflow 3 **already does this by default**: the artifact is `model.skops`. The kernel's `skops` pin
is not a foothold for a future migration, it is the working serializer, and removing it as "unused" would
have broken model logging outright.

Recording the error rather than quietly fixing it, because it is the instructive part: *every* participant
here — the architecture review, the ADR draft, the reviewer — asserted the artifact was a pickle, from the
shape of the code, confidently, and none of them ran it. That is the failure ADR-0026 named, and it is why
decision 5 makes the empirical check the authority rather than a formality.

What *remains* genuinely open is the narrower question the skops proposal was really pointing at: MLflow
still deserializes pickle artifacts when it meets them (`MLFLOW_ALLOW_PICKLE_DESERIALIZATION` defaults to
true), so the serving path retains a path to executing arbitrary code from a notebook-produced file. That
is a **trust** decision, not a parity one, and it is deferred below.

## Consequences

### Positive

- The boundary is named, and the next bump that would break it fails a check instead of shipping.
- The proof is a real artifact making the real journey, not an assertion that two strings are equal.
- **The gate earned its keep before it merged.** Building it found three things that reading the code
  had not: that MLflow serializes sklearn with skops rather than pickle (so the ADR's premise was
  wrong), that the serving image's serializer was unpinned, and that **xgboost 2.x cannot save under
  scikit-learn 1.9 — a break already sitting in the serving image**, invisible because it is on the save
  path and nothing had ever saved an xgboost model. None of these are findable by inspection; all three
  fell out of running it once.
- **The rule survives torch.** A framework the serving side cannot honour is refused with a reason, at the
  gate, today — before the adapter contract exists — instead of being silently mis-served.
- `ml_service` can still take a scikit-learn patch without a notebook release; the kernel can still add a
  library without asking the serving team.
- The kernel's Python 3.14 / numpy 2 bump — held at 3.12 for exactly this reason — is unblocked, and the
  last two images of the docker-images group with it.
- The operator is told the truth about a model whose environment no longer matches, in the place they
  already look, without a fired alert they cannot label.

### Negative

- **The empirical check is the most expensive thing this ADR asks for**: it stands up both environments and
  moves an artifact between them. It stays affordable only by keeping what it trains trivial — it is testing
  the *boundary*, not the model.
- **The scikit-learn rule is a judgement, not a proof.** Patch drift is permitted and *can* in principle
  break an artifact. Decision 7's middle state exists precisely because that residual risk is real.
- **The registration verdict goes stale**, which is why decision 6 pays for a second gate. Even so, a model
  already deployed when its serving environment is rebuilt is not re-checked — only a re-deployment is.
  Closing that needs a background reconciler, deferred below.
- **Pre-existing broken models keep serving.** This ADR marks them; it cannot fix them, because the platform
  has no training service to fix them with (ADR-0022 dec 4).
- **Torch and autoencoders are not servable today, and this ADR does not make them so** — it makes their
  absence *honest* rather than silent. The requirement is real and now sits, correctly scoped, on the
  model-adapter contract. Naming a gap is not closing it.
- **The kernel stack bump reaches every data scientist at their next spawn** (ADR-0020 dec 1). Notebooks
  written against numpy 1 behaviour may need edits — the cost of the environment being the image's and not
  the volume's, which is ADR-0020 working as intended.

## Deferred decisions

- **The model-adapter contract (ADR-0010's, now come due).** How a flavor is *added*: the adapter signature,
  in-process versus out-of-process policy per flavor, packaging of optional flavor sidecars, and how the
  supported set is declared to an operator. **This is the live requirement behind torch and autoencoders**
  (decision 4). It is deferred from *this* record, not from the project's work.
- **Refusing pickle artifacts outright (the *trust* half of this boundary).** Alternative F. sklearn
  models are already skops; but MLflow will still deserialize a pickle when it meets one
  (`MLFLOW_ALLOW_PICKLE_DESERIALIZATION` defaults true), so the serving path retains a route to executing
  arbitrary code from a notebook-produced file. Whether to turn that off — and what it breaks — is its
  own record.
- **A background reconciler** re-evaluating decision 7's property for already-deployed models when the
  serving environment changes, closing the staleness in Consequences. If it ever warrants a proactive
  notification, that is a **new, mechanical** condition — never ADR-0022's statistical lane (decision 8).
- **The rest of ADR-0011's supply chain.** In-environment `pip install`, base image contents beyond the
  artifact path, curated-notebook promotion (decision 11).
- **The analytics kernel's versioning policy.** Its own operational record, when it warrants one
  (decision 12).

## References

- ADR-0011 — the deferral decision 11 resolves; ADR-0000 — the discipline that forbade settling this inside
  a dependency bump.
- ADR-0008 dec 3 / ADR-0010 dec 4 + its deferred model-adapter contract — the plugin category and the
  out-of-process permission that decision 4 hands the open framework set to.
- ADR-0019 — the gateway the artifact crosses; ADR-0010 dec 6 / ADR-0021 — the in-process warm-cache posture
  that makes *per-model* environments (Alternative C) the wrong unit and *per-flavor* adapters the right one.
- ADR-0022 dec 4 — the alert lane decision 8 declines to reuse, and the "no training service" fact decision
  10 rests on.
- ADR-0026 — the gate discipline decision 5 inherits: the test is the gate.
- Implementation: the two checks of decision 5 live in CI; the refusals and the model property of decisions
  6-7 live in `services/ml_service`. Which files, which status codes, which column and which states it takes
  are the implementation's to choose — **this ADR fixes the properties they must establish, not the commands
  by which they establish them.**
- Issue #220 — the record of the gap; the kernel Dockerfiles carry the hold that led here.
