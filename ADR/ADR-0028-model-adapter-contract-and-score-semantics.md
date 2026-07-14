<!--
SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
SPDX-License-Identifier: CC-BY-SA-4.0
-->

# ADR-0028 — A model declares what its output means; the platform never guesses

- **ID:** ADR-0028
- **Status:** Accepted
- **Date:** 2026-07-14
- **Project:** IndustryFlow
- **Parent:** [ADR-0010](ADR-0010-extension-plugin-mechanism.md) (whose deferred **model-adapter contract** — *"a third contract … its signature is not fixed here"* — this closes)
- **Companions:** [ADR-0027](ADR-0027-model-artifact-supply-chain-parity.md) (the gate that refuses what this environment cannot honour; this ADR says what "can honour" means for *scoring*), [ADR-0008](ADR-0008-extension-and-plugin-interface.md) (which names *model adapters* as a plugin category, and whose in-process default this inherits)
- **Related:** [ADR-0021](ADR-0021-model-drift-monitoring.md) (the lane that trusts these scores enough to alert on them), [ADR-0022](ADR-0022-concept-drift-operator-feedback.md) (the operator who labels those alerts), [ADR-0001](ADR-0001-industryflow-framing.md) (why a self-hoster who never trains a torch model must not carry a CUDA layer)

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

## References

- ADR-0010 — the registry this completes, and the deferred model-adapter contract it names.
- ADR-0027 — the gate that refuses an artifact this environment cannot honour; decision 4 extends it from
  *loading* to *meaning*.
- ADR-0008 — model adapters as a plugin category; the in-process default decision 6 inherits.
- ADR-0021 / ADR-0022 — what consumes these scores, and why a wrong one is worse than none.
- Issue #236 — every IsolationForest prediction scored as an anomaly. The evidence for decision 2, and
  the reason this record exists rather than a one-line patch.
