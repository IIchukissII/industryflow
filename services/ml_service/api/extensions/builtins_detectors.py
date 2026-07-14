# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Generic anomaly detectors shipped with the platform (ADR-0010's second contract, completed by
ADR-0028).

A detector's whole job is to turn a model's output into a 0–1 anomaly score. ADR-0028 dec 2 fixes how:
**the semantics are declared, never inferred from the output.** They cannot be inferred — the
information is not there:

    IsolationForest.predict() -> +1 means NORMAL,  -1 means anomaly
    XGBoost(binary).predict() -> +1 means ANOMALY,  0 means normal

The same integer means opposite things. The previous version of this module tried to read the label
anyway and mapped BOTH `+1` and `-1` to `score = 1.0`. IsolationForest has no `predict_proba`, so it
landed in exactly that branch: **every IsolationForest prediction — including a point in the dead
centre of its training distribution — scored 1.0 and fired as an anomaly** (#236). A 100%
false-positive rate on the platform's default detector family, invisible for months because nothing
ever asserted what a *normal* reading should score.

So no detector here reads a label to decide meaning. Each is registered against the semantics it
implements, and a model whose semantics cannot be established is REFUSED (UninterpretableModel)
rather than scored with a number nobody can justify — ADR-0021 raises alerts on these numbers, and a
silent wrong score poisons the very feedback loop (ADR-0022) built to catch bad models.

None of these carry domain knowledge. A domain registers its own detector the same way.
"""
import logging

from . import (
    ANOMALY_PROBABILITY,
    OUTLIER_SCORE,
    RECONSTRUCTION_ERROR,
    DetectionResult,
    UninterpretableModel,
    register_detector,
)

logger = logging.getLogger(__name__)

try:  # numpy is present in the ML service; keep the SDK importable without it.
    import numpy as np
except ImportError:  # pragma: no cover - the service always has numpy
    np = None


def _estimator(model):
    """Reach the real estimator; MLflow's pyfunc wrapper hides it behind `_model_impl`."""
    impl = getattr(model, "_model_impl", model)
    # mlflow's sklearn pyfunc wrapper nests it once more.
    return getattr(impl, "sklearn_model", impl)


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


@register_detector(
    "sklearn",
    semantics=ANOMALY_PROBABILITY,
    handles_flavors=["mlflow.sklearn", "mlflow.xgboost"],
)
async def sklearn_detector(features, model, threshold, ctx) -> DetectionResult:
    """Score a scikit-learn-family model by what it can actually DO, never by what its label looks like.

    The compatibility name: every model registered before ADR-0028 names `sklearn`, so this keeps
    working for them — but it now dispatches on the estimator's *capability*, in a fixed order, and
    each branch knows the semantics it is reading:

      1. `predict_proba`  -> a calibrated probability of the anomalous class.
      2. `decision_function` -> a continuous OUTLIER score (IsolationForest, OneClassSVM,
         LocalOutlierFactor). This is the branch #236 never reached, because the old code read
         `predict()`'s ±1 label instead — and could not tell IsolationForest's "+1 = normal" from
         XGBoost's "+1 = anomaly".
      3. a float prediction -> the model already emits a score and means it.

    A model that offers none of these is REFUSED, not guessed at.
    """
    est = _estimator(model)

    if hasattr(est, "predict_proba"):
        proba = est.predict_proba(features)
        # Column 1 is the positive (anomalous) class for a binary classifier; a single-column
        # output is already the probability of that class.
        score = float(proba[0][1]) if proba.shape[1] > 1 else float(proba[0][0])
        return DetectionResult(
            score=_clamp(score),
            is_anomaly=_clamp(score) >= threshold,
            detail={"semantics": ANOMALY_PROBABILITY},
        )

    if hasattr(est, "decision_function"):
        return _outlier_result(float(np.asarray(est.decision_function(features)).ravel()[0]), threshold)

    prediction = np.asarray(model.predict(features)).ravel()[0]
    if isinstance(prediction, (float, np.floating)):
        return DetectionResult(
            score=_clamp(prediction),
            is_anomaly=_clamp(prediction) >= threshold,
            detail={"semantics": "direct_score"},
        )

    # An integer label with no probability and no outlier score. This is exactly the case that
    # produced #236, and the honest answer is that we do not know what it means: `1` is normal to an
    # IsolationForest and anomalous to a classifier, and nothing in the artifact says which.
    raise UninterpretableModel(
        f"this model returns the bare label {prediction!r} and exposes neither predict_proba nor "
        f"decision_function — so what the label MEANS cannot be established "
        f"(+1 is 'normal' to an IsolationForest and 'anomaly' to a classifier). Register a detector "
        f"that declares the semantics of this model rather than having the platform guess (ADR-0028)."
    )


@register_detector(
    "outlier",
    semantics=OUTLIER_SCORE,
    handles_flavors=["mlflow.sklearn"],
)
async def outlier_detector(features, model, threshold, ctx) -> DetectionResult:
    """A novelty/outlier model scored from its CONTINUOUS score — IsolationForest and friends.

    Named explicitly so a model can say what it is, instead of relying on `sklearn`'s capability
    dispatch to work it out. Same maths; a declaration rather than a deduction.
    """
    est = _estimator(model)
    if not hasattr(est, "decision_function"):
        raise UninterpretableModel(
            "the 'outlier' detector scores from `decision_function`, whose SIGN carries the "
            "inlier/outlier boundary; this model does not expose one. (`score_samples` is not a "
            "substitute — it is not centred on the boundary, so its sign says nothing.)"
        )
    return _outlier_result(float(np.asarray(est.decision_function(features)).ravel()[0]), threshold)


def _outlier_result(raw: float, threshold: float) -> DetectionResult:
    """Map an sklearn outlier score to 0–1, where 1 is most anomalous.

    sklearn's convention for `decision_function`: NEGATIVE is the outlier side of the boundary,
    positive is the inlier side, and the magnitude is a distance from it. So the sign carries the
    verdict and the magnitude carries the confidence — which is why reading `predict()`'s ±1 label
    instead threw away everything that mattered.

    `score_samples` is deliberately NOT used: it is the same ranking shifted by the model's own offset,
    so it is not centred on the boundary (a perfectly normal point scores around -0.4, not 0). Feeding
    it to the squash below would put every point on the outlier side — a subtler rerun of #236, which
    is exactly what the "what does a NORMAL reading score?" test caught.

    A logistic squash keeps it monotone and bounded: a point exactly on the boundary scores 0.5, deep
    inliers tend to 0, deep outliers tend to 1. It is a rank-preserving normalisation, NOT a
    calibrated probability, and it is reported as such — a threshold set on it means "how far past the
    boundary", not "how likely".

    THE TEMPERATURE IS NOT DECORATION, AND ITS ABSENCE WAS A BUG. `decision_function` is O(0.1) in
    practice (a normal point sits near +0.12, a clear outlier near -0.25), so squashing the raw value
    directly pins every score into ~[0.45, 0.56] — and the platform's DEFAULT anomaly threshold is
    **0.85** (`InferenceRequest.threshold`, and `anomaly_threshold` in the alert worker's rules). A far
    outlier would have scored 0.54 and never fired: #236 inverted, from all-false-positives to
    all-false-NEGATIVES. Box validation caught it; the unit tests had not, because they asserted
    against a 0.5 threshold — my assumption — rather than the one the platform actually ships.

    Dividing by the typical magnitude of the score restores a usable range: a normal reading lands
    near 0.12, a clear outlier above 0.95, and 0.85 means "well past the boundary" rather than
    "unreachable".

    Why a fixed temperature is defensible here when a fixed scale is REFUSED for the autoencoder
    below: `decision_function` is scale-invariant by construction — it is a ratio of average path
    lengths, so its magnitude is comparable across IsolationForest models and datasets (a dataset
    scaled by 50x produces the same range). An autoencoder's reconstruction error is not: it carries
    the units of the data, and differs by orders of magnitude between models. There, the scale must
    come from the model; here, it is a property of the algorithm.
    """
    # The typical |decision_function| of a decided point. Empirically ~0.12-0.25 across dimensions and
    # data scales; 0.06 puts a clear outlier comfortably past the 0.85 default and a normal reading
    # well under it.
    TEMPERATURE = 0.06
    score = 1.0 / (1.0 + np.exp(raw / TEMPERATURE))  # raw < 0 (outlier) -> > 0.5; raw > 0 -> < 0.5
    score = _clamp(score)
    return DetectionResult(
        score=score,
        is_anomaly=score >= threshold,
        detail={"semantics": OUTLIER_SCORE, "raw_outlier_score": raw},
    )


@register_detector(
    "autoencoder",
    semantics=RECONSTRUCTION_ERROR,
    handles_flavors=["mlflow.sklearn", "mlflow.pyfunc"],
)
async def autoencoder_detector(features, model, threshold, ctx) -> DetectionResult:
    """Score an autoencoder by how badly it reconstructs its input.

    The case that proves ADR-0028's point rather than merely joining it: an autoencoder emits NO
    verdict. Its output is a reconstruction of the input, and the anomaly signal is the distance
    between the two — a fact about the *relationship* between input and output, which no amount of
    inspecting the output alone could ever recover. It needs no new dependency: a scikit-learn
    autoencoder (MLPRegressor, or any estimator trained to reproduce its input) already loads, already
    lands in the warm cache, and already passes ADR-0027's gate.

    NORMALISATION IS THE HONEST DIFFICULTY. Reconstruction error is unbounded and its scale is a
    property of the model and its data — an error of 0.4 is catastrophic for one model and unremarkable
    for another. The only defensible scale is one the model carries: the error it saw on its own
    training data. Where the model record carries no such scale, this detector REFUSES rather than
    inventing one, because a made-up scale is a made-up alert (and thresholds are what operators tune).
    """
    reconstruction = np.asarray(model.predict(features))
    original = np.asarray(features)

    if reconstruction.shape != original.shape:
        raise UninterpretableModel(
            f"an autoencoder must reconstruct its input, but this model mapped an input of shape "
            f"{original.shape} to an output of shape {reconstruction.shape}. It is not an "
            f"autoencoder, or it is not the model this rule thinks it is."
        )

    mse = float(np.mean((original - reconstruction) ** 2))

    scale = (ctx.reconstruction_scale if getattr(ctx, "reconstruction_scale", None) else None)
    if not scale or scale <= 0:
        raise UninterpretableModel(
            "this autoencoder has no reconstruction-error scale recorded, so its error cannot be "
            "turned into a 0-1 score: an MSE of "
            f"{mse:.6g} is catastrophic for one model and unremarkable for another. Register the "
            "model with the reconstruction error it saw on its own training data (ADR-0028 deferred: "
            "captured alongside ADR-0021's reference profile)."
        )

    # Relative to the model's own training-time error: at the recorded scale the score is 0.5, well
    # below it tends to 0, well above tends to 1. Monotone in the error, and bounded.
    score = _clamp(mse / (mse + scale))
    return DetectionResult(
        score=score,
        is_anomaly=score >= threshold,
        detail={
            "semantics": RECONSTRUCTION_ERROR,
            "reconstruction_error": mse,
            "training_scale": scale,
        },
    )
