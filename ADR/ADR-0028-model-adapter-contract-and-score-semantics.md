<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0028 — A model declares what its output means; the platform never guesses (rev 2)

- **ID:** ADR-0028
- **Status:** Accepted
- **Date:** 2026-07-14 (rev 1: 2026-10-13; rev 2: 2026-10-17)
- **Project:** IndustryFlow
- **Parent:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (whose deferred **model-adapter contract** — *"a third contract … its signature is not fixed here"* — this closes)
- **Companions:** [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (the gate that refuses what this environment cannot honour; this ADR says what "can honour" means for *scoring*), [ADR-0008](ADR-0008-extension-and-plugin-interface.md) (which names *model adapters* as a plugin category, and whose in-process default this inherits)
- **Related:** [ADR-0021](ADR-0021-model-drift-monitoring.md) (the lane that trusts these scores enough to alert on them), [ADR-0022](ADR-0022-concept-drift-operator-feedback.md) (the operator who labels those alerts), [ADR-0001](ADR-0001-industryflow-framing.md) (why a self-hoster who never trains a torch model must not carry a CUDA layer)
- **Supersedes:** ADR-0028 rev 0 (2026-07-14 — made the detector a fact of the model record without saying when it is fixed, listed `direct_score` with no detector implementing it, and left the compatibility detector able to read a continuous output as a score; rev 1 decides all three, decisions 1 and 3); rev 1 (2026-10-13 — named the model's raw sample score beside its boundary-relative score as one outlier semantics; rev 2 restricts `outlier_score` to a score centred on the decision boundary, decision 1)

## Context and problem

ADR-0010 dec 6 put anomaly detection behind a registry: the model record names a `detector`, and the
platform dispatches to it. That was right. What was never decided is the thing the detector actually
does — **turn a model's output into a 0–1 anomaly score** — and in the absence of a decision, the
built-in detector *infers* it, by looking at the shape and value of what `predict()` returned.

**That inference is not merely fragile. It is impossible, and it has been silently wrong in
production.** The conventions of the model families this platform already serves contradict each
other outright:

| model | `predict()` returns `1` | `predict()` returns `-1` | `predict()` returns `0` |
|---|---|---|---|
| **IsolationForest** | **normal** | anomaly | — |
| **XGBoost** (binary) | **anomaly** | — | normal |

The same integer means *opposite things*. No amount of sniffing resolves it, and the code did not:
`builtins_detectors.py` mapped **both** `-1` and `1` to `score = 1.0`. IsolationForest has no
`predict_proba`, so it landed in exactly that branch — and therefore **every IsolationForest
prediction, including a point in the dead centre of its training distribution, scored 1.0 and fired
as an anomaly.** A 100% false-positive rate, on the platform's default detector family, invisible
because nothing ever asserted what a *normal* reading should score. (#236, found while building
ADR-0027's round-trip gate.)

The autoencoder is the case that proves the point rather than merely adding to it: its anomaly signal
is **reconstruction error** — the distance between input and output. It emits no label at all. There is
no value of `predict()[0]` that a sniffing detector could interpret correctly, because the answer is
not in the prediction; it is in the *relationship between the prediction and the input*.

And this is now the live requirement. **xgboost is not a privileged model type — it is one of many.**
The platform must serve autoencoders, and torch models, and whatever a data scientist reaches for
next. ADR-0027 established that an artifact the serving environment cannot honour is refused at the
gate rather than mis-served; it explicitly handed *how a new flavor becomes servable* to this record.

**Revision 1 (2026-10-13).** Review found that decision 2 was kept at the front door and broken
behind it, in three ways. *The detector was not a durable fact.* The record said the registry gains
it, but the model record had nowhere to keep it, and serving assumed the compatibility detector for
every model when none was named — so the semantics a model was admitted under were not necessarily
the semantics it was scored under. *`direct_score` had no detector.* Decision 1 lists it; nothing
declared it, so a model that honestly emitted a score could only reach it through the compatibility
detector's guess. *The compatibility detector still guessed.*
Given a model with neither a probability nor an outlier score, it took a continuous prediction as if
it were a score — which is inferring semantics from a dtype, the act decision 2 forbids. Rev 1 fixes
when the detector is decided and what serving does with it (decision 1), adds the direct-score
detector, and closes the compatibility detector's last guess (decision 3).

**Revision 2 (2026-10-17).** Decision 1 defined `outlier_score` by two of IsolationForest's outputs as
if they carried one meaning. They do not. The decision-boundary score is centred on the boundary: its
sign is the verdict and its magnitude the distance from it. The raw sample score is the same ranking
shifted by an offset the model learned, so a perfectly normal reading sits well below zero and its sign
says nothing. A detector that read the second as the first would put every reading on the anomalous
side, which is #236 again in a subtler form, and the built-in outlier detector already refuses it. The
definition invited exactly the inference decision 2 forbids. Rev 2 restates the semantics as the score
centred on the boundary (decision 1).

## Decision drivers

- **A wrong score is worse than no score.** ADR-0021 raises alerts on these numbers and ADR-0022 asks
  an operator to label those alerts true or false. A detector that scores everything 1.0 does not just
  fail — it poisons the feedback loop built to detect failure.
- **Meaning cannot be recovered from a value.** `1` is normal or anomalous depending on a fact about
  the model that the number does not carry. Whatever the platform needs to know, the model must *say*.
- **The framework set is open; this image is not.** A self-hoster who never trains a torch model must
  not carry a multi-gigabyte CUDA layer (ADR-0001), and ml_service is one image with one pinned stack
  (ADR-0027) serving models from a warm in-process cache (ADR-0021).
- **MLflow already loads everything.** `pyfunc` dispatches on the artifact's `loader_module` and hands
  back a uniform `predict()`. The platform does not need to reinvent loading — only to interpret.

## Decision

1. **A model's output has *declared semantics*, and the detector that scores it declares which
   semantics it implements.** The registry gains this as a first-class fact rather than a guess. The
   semantics on record today:

   - **`anomaly_probability`** — a calibrated probability that the sample is anomalous.
   - **`outlier_score`** — a continuous novelty/outlier score (IsolationForest's `decision_function` /
     `score_samples`), where *more negative is more anomalous* and the label is a thresholding of it.
   - **`reconstruction_error`** — the distance between input and the model's reconstruction of it. The
     model emits no verdict; the *detector* computes the signal. This is the autoencoder.
   - **`direct_score`** — the model already emits a 0–1 anomaly score and means it.

   The list is open: a new semantics is a new registration, not a core edit (ADR-0010 dec 1).

   > **rev 2 (2026-10-17): `outlier_score` is a continuous score centred on the model's own decision
   > boundary.** Its sign carries the inlier/outlier verdict, with negative on the outlier side, and
   > its magnitude carries the distance from the boundary; the label is a thresholding of it at zero.
   > A raw sample score that is offset from the boundary by a quantity the model learned is **not**
   > this semantics: its sign means nothing, and a detector that took it for one would score normal
   > readings as anomalous. A model that exposes only such a score does not offer `outlier_score`, and
   > a detector implementing `outlier_score` refuses it rather than re-centring it by a guess.

   > **rev 1 (2026-10-13): the detector is persisted per model at registration, and serving
   > dispatches on it.** Which detector scores a model is decided **once, at registration**, from what
   > the registration declares — a named detector, or for an uploaded model its declared semantics and
   > flavor (ADR-0030 dec 5) — and only if that detector is registered in this deployment and handles
   > the model's flavor. The choice is then **recorded on the model** as a first-class fact, and the
   > detector *is* the record of the model's semantics. Serving reads it and dispatches to it; it
   > never re-derives the choice from the artifact or its output. A model with no recorded detector,
   > or one naming a detector this deployment no longer has, is refused at deployment and at scoring
   > rather than scored by a substitute. Models registered before this revision are recorded with the
   > compatibility detector of decision 3, which is what every one of them named.

2. **The platform never infers semantics from a prediction's value or dtype, and this is the decision,
   not an implementation preference.** Sniffing is what produced #236, and it could not have produced
   anything else — the information required is not present in the output. A detector that cannot
   establish its model's semantics **refuses to score** rather than returning a number it cannot
   justify. A refusal is legible; a confident wrong score is not.

3. **The built-in detectors are split along the semantics they implement, and the label is never the
   signal.** IsolationForest is scored from its continuous outlier score, never from its ±1 verdict; a
   classifier from its probability; an autoencoder from reconstruction error. `sklearn` remains as a
   compatibility name — every model registered before this ADR names it — but it now *dispatches on
   what the model can actually do* rather than on what its output happens to look like, and it never
   again maps two opposite labels to the same score.

   > **rev 1 (2026-10-13): the compatibility detector refuses undeclared continuous output; a
   > direct-score detector exists for declared bounded scores.** The compatibility detector scores a
   > model only through a capability whose meaning is fixed by the capability itself — a class
   > probability, or a continuous outlier score — and **refuses** any model that offers neither. In
   > particular it never reads a continuous prediction as a score: a float is a dtype, not a meaning,
   > and treating it as one is the inference decision 2 forbids. A model whose output already *is* a
   > 0–1 anomaly score says so by declaring `direct_score`, and is scored by a **direct-score
   > detector**, which passes the value through only if it is finite and on the declared bounded
   > scale, and refuses it otherwise — it never clamps an out-of-range value into range. Every
   > semantics on decision 1's list now has a detector that implements it.

4. **An adapter declares which artifact flavors it can score.** ADR-0027's gate asks "can this
   environment satisfy what the artifact declares?"; this closes the loop by also asking **"is there
   anything here that knows what this model's output MEANS?"** An artifact whose flavor no registered
   adapter handles is refused at the gate, with a reason — the same honest refusal, one level up.

5. **The supported set is DISCOVERED from the registry, not maintained as a list.** What this
   deployment can serve is the union of the adapters registered in it (built-ins plus whatever the
   operator loaded via `EXTENSION_MODULES`) and what the environment can actually import. It is
   therefore introspectable, and it is *served* — an operator can ask what this deployment will accept
   **before** they train against it, rather than discovering the answer at a failed deploy. A list
   maintained by hand would be a second source of truth, and would drift the first time an operator
   added an adapter.

6. **In-process remains the default (ADR-0008), and the autoencoder requirement is met inside it.**
   A scikit-learn autoencoder (`MLPRegressor`, or any estimator that reconstructs its input) already
   loads, already lands in the warm cache, and already passes ADR-0027's gate. What was missing was
   never a runtime — it was decision 1. So the product requirement lands with **no new dependency, no
   new image, and no new serving path**.

7. **Torch, GPU, and any framework the image does not carry are OUT OF SCOPE here — and are already
   handled honestly.** ADR-0027's gate refuses them at registration and deployment with a reason. This
   ADR does **not** decide the out-of-process serving boundary: the sidecar protocol, the routing of an
   artifact to one of several environments, the second parity boundary, and the Helm packaging of an
   optional flavor are a larger decision than this one and earn their own record. Naming it as deferred
   is the point — ADR-0010 deferred *this* contract once, and it went unwritten for long enough that a
   100% false-positive rate lived inside the gap.

## Alternatives considered

**A. Keep inferring, but fix the IsolationForest branch.** *Rejected.* It is the smallest possible
change and it is what a hotfix would do. But it treats #236 as a typo, when #236 is a *category error*:
the meaning of a model's output is not derivable from that output. Patch the `1` case and the
autoencoder still cannot be scored, the next model family collides again, and the next reader has no
way to know which convention the code is currently assuming. The bug is a symptom; the guess is the
disease.

**B. One detector per model class, dispatched by `isinstance`.** *Rejected.* It swaps one form of
sniffing for another — inspecting the object rather than its output — and it re-couples the core to a
list of the model classes it knows about, which is precisely what ADR-0010 dec 1 forbids. It also fails
the case that matters: two `MLPRegressor`s, one an autoencoder and one a plain regressor, are the same
class with different semantics.

**C. Put torch in the serving image so every framework is in-process.** *Rejected*, and it is the
tempting answer to "support torch". It charges a multi-gigabyte CUDA layer to every self-hoster who
never trains a torch model (ADR-0001), drags torch into ADR-0027's parity contract — so the notebook
kernel and the serving image must now move torch in lockstep — and makes the dependency resolution of
the whole platform hostage to one framework's release cadence. An open framework set cannot be served
by enumeration.

**D. Ship the sidecar now, with the autoencoder as its first tenant.** *Rejected as sequencing.* The
autoencoder does not need it — decision 6 — so building it now would mean designing a serving protocol,
an image, a routing rule and a Helm story in order to deliver something that already works in-process.
The sidecar's real driver is torch/GPU, and it should be designed when that is the requirement being
paid for, not smuggled in behind a requirement it does not serve.

**E. Re-select the detector at load, from the artifact *(rev 1)*.** *Rejected:* it keeps the
registry and serving in agreement only for as long as the selection logic does not change, and makes
the semantics a model was admitted under different from the semantics it may be scored under. A
fact decided once and recorded cannot drift between the two.

**F. Let the compatibility detector read a float prediction as a score, as a last resort
*(rev 1)*.** *Rejected:* it is decision 2's sniffing in its last remaining form. A model that means
its output as a score can declare `direct_score` and be held to the bounds; one that cannot declare
it is exactly the model whose output the platform has no basis to interpret.

## Consequences

### Positive

- **#236 is fixed at the root**: no detector maps two opposite labels to one score, because no detector
  reads labels to decide meaning any more.
- **Autoencoders are servable with no new dependency**, meeting the product requirement immediately.
- **xgboost stops being privileged** — it is one registration among several, dispatched by the model
  record, exactly like any extension's own detector.
- A model whose meaning nothing here understands is **refused, with a reason**, instead of scored
  wrongly. The gate ADR-0027 built now covers *interpretation* and not merely *loading*.
- An operator can ask what a deployment can serve, rather than inferring it from a failure.

### Negative

- **Every model already registered names the `sklearn` detector**, which now dispatches on capability
  rather than on output shape. A model whose scores were *accidentally* correct under the old branch —
  an XGBoost binary classifier, where `1` really did mean anomaly — is unaffected; an IsolationForest's
  scores will **change**, because they were wrong. Thresholds tuned against the broken behaviour (any
  threshold ≤ 1.0 fired always) are meaningless and must be re-set. This is a correction, and it will
  look like a behaviour change to anyone who trusted the old numbers.
- **Reconstruction error is unbounded**, and the 0–1 contract is not. Normalising it needs a scale, and
  the honest scale is a property of the *model* (the reconstruction error it saw on its own training
  data), not a constant. Where a model does not carry one, the detector must say so rather than invent
  one — which means some autoencoders will be registered but not yet scoreable. Deferred below.
- **Torch is still not servable.** This ADR makes its absence honest; it does not close it.
- **rev 1:** a model that was being scored from a raw continuous prediction is now refused until it
  is re-registered with a declared semantics, and changing a model's detector means registering it
  again rather than editing a heuristic.

- **rev 2:** a model that offers only an offset sample score, and no boundary-centred one, is refused
  by the outlier detectors; it needs a detector that declares how to centre that score before it can be
  scored.

## Deferred decisions

- **The out-of-process serving boundary** (decision 7): the sidecar protocol, routing an artifact to one
  of several environments, the second parity boundary it creates, and the Helm packaging of an optional
  flavor. This is torch's and GPU's ADR. **It must not be deferred silently a second time** — ADR-0010's
  deferral of *this* contract is what let #236 live.
- **Where the reconstruction-error scale comes from.** Capturing it at training time (the p99 error over
  the training window) mirrors ADR-0021's `reference_profile` exactly and is the obvious home; whether it
  belongs *in* that profile or beside it is a schema decision this ADR does not force.
- **Whether a drift signal should carry its semantics.** ADR-0021 compares prediction distributions; two
  models with different score semantics are not comparable, and the drift lane does not currently know
  that.

## Resolved since acceptance

- **Whether a drift signal carries its semantics** (deferred above) is resolved by ADR-0021 rev 1: both
  sides of a prediction-drift comparison hold the model's declared detector's scores, on that
  detector's scale, so a model is only ever compared with a baseline of the same semantics.

## Added after acceptance

- **Where the reconstruction-error scale comes from** (deferred above) remains open as a schema
  question. The build carries the scale as a training-time figure supplied with the model at
  registration, and the reconstruction-error detector refuses a model registered without one, as
  the Negative consequence requires.

## References

- ADR-0010 — the registry this completes, and the deferred model-adapter contract it names.
- ADR-0027 — the gate that refuses an artifact this environment cannot honour; decision 4 extends it from
  *loading* to *meaning*.
- ADR-0008 — model adapters as a plugin category; the in-process default decision 6 inherits.
- ADR-0021 / ADR-0022 — what consumes these scores, and why a wrong one is worse than none.
- Issue #236 — every IsolationForest prediction scored as an anomaly. The evidence for decision 2, and
  the reason this record exists rather than a one-line patch.
