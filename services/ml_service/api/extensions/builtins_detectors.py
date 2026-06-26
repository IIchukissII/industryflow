# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Generic anomaly detectors shipped with the platform (ADR-0010, second contract).

The built-in ``sklearn`` detector turns the output of a scikit-learn-family model
(IsolationForest, XGBoost, or any model exposing predict_proba) into a 0–1 anomaly score
and a threshold decision. It carries no domain knowledge; a domain registers its own
detector type the same way.
"""
import logging

from . import register_detector, DetectionResult

logger = logging.getLogger(__name__)

try:  # numpy is present in the ML service; keep the SDK importable without it.
    import numpy as np
    _INT_TYPES = (int, np.integer)
except ImportError:
    _INT_TYPES = (int,)


@register_detector("sklearn")
async def sklearn_detector(features, model, threshold, ctx) -> DetectionResult:
    """Score sensor features with a loaded scikit-learn-family model."""
    prediction = model.predict(features)

    # MLflow's pyfunc wrapper hides the estimator; unwrap to reach predict_proba.
    actual_model = getattr(model, "_model_impl", model)

    if hasattr(actual_model, "predict_proba"):
        proba = actual_model.predict_proba(features)
        score = float(proba[0][1]) if proba.shape[1] > 1 else float(proba[0][0])
    elif isinstance(prediction[0], _INT_TYPES):
        # Binary label conventions: IsolationForest (-1 anomaly / 1 normal) and
        # XGBoost (1 anomaly / 0 normal).
        if prediction[0] == -1:
            score = 1.0
        elif prediction[0] == 1:
            score = 1.0
        elif prediction[0] == 0:
            score = 0.0
        else:
            score = float(abs(prediction[0]))
    else:
        score = float(prediction[0])

    score = max(0.0, min(1.0, score))
    return DetectionResult(score=score, is_anomaly=score >= threshold)
