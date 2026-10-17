# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reference-profile format (ADR-0021 dec 4).

A profile snapshots the distribution of each input feature over the training window. It is supplied
in the registration request; the platform builds none itself (``build_reference_profile`` is the
format's reference implementation for a training flow to call). Drift is measured as the divergence
of a recent window from this baseline, computed by `evidently` in the drift endpoint (ADR-0021 dec 3).

`evidently` measures drift between a *reference dataset* and a *current dataset*, so the
profile stores a **capped, deterministically down-sampled reference sample** of the
training-window columns — compact enough for JSONB, large enough for the statistical
tests, and tenant-scoped (it is the tenant's own training data, stored with the tenant's
own model). Optionally it carries the model's own training-window output scores as a
`prediction_column`, so prediction drift falls out of the same reference/current
comparison as data drift.

A model with no reference profile reports "drift unavailable" until retrained with one.
Numeric features only in v1 (sensor streams are numeric).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

import numpy as np

from extensions import SCORE_RANGE

# Bumped when the on-disk profile shape changes so the scorer refuses a shape it does not
# understand rather than silently mis-comparing. v2 = evidently reference sample
# (v1 was a histogram snapshot, superseded before release to keep the compute engine
# consistent with ADR-0021's evidently choice).
REFERENCE_PROFILE_VERSION = 2

# Cap on stored reference rows. Evidently's tests are stable well below this; the cap keeps
# the JSONB payload bounded regardless of training-window size.
DEFAULT_MAX_SAMPLE_ROWS = 5000

# Default name for the model's own output-score column when prediction drift is captured.
DEFAULT_PREDICTION_COLUMN = "prediction_score"


def _sample_indices(n_rows: int, max_rows: int) -> np.ndarray:
    """Deterministic, evenly-spaced down-sample indices (no RNG → reproducible)."""
    if n_rows <= max_rows:
        return np.arange(n_rows)
    return np.linspace(0, n_rows - 1, max_rows).astype(int)


def _clean_column(values: Sequence[float], idx: np.ndarray) -> List[Optional[float]]:
    """Down-sample one column and JSON-normalize (numpy float; NaN/inf -> None)."""
    arr = np.asarray(values, dtype="float64")[idx]
    out: List[Optional[float]] = []
    for v in arr:
        out.append(float(v) if math.isfinite(v) else None)
    return out


def _score_refusal(values: Sequence[Any]) -> Optional[str]:
    """Why ``values`` are not detector scores on the declared scale, or ``None`` if they are."""
    low, high = SCORE_RANGE
    if not values:
        return "it holds no scores"
    for v in values:
        if isinstance(v, bool) or not isinstance(v, (int, float, np.integer, np.floating)):
            return f"it holds {v!r}, which is not a score"
        if not math.isfinite(float(v)):
            return f"it holds {v!r}, which is not a finite score"
        if not low <= float(v) <= high:
            return (f"it holds {v!r}, outside the detector's declared scale [{low:g}, {high:g}] — "
                    f"a label, a raw model output or another quantity, not a detector score")
    return None


def prediction_column_refusal(profile: Mapping[str, Any]) -> Optional[str]:
    """Why ``profile``'s prediction column is not a prediction-drift baseline — ``None`` if it is one,
    or if the profile carries no prediction column (data drift needs none).

    ADR-0021 dec 4: the column holds what the current side holds — the declared detector's
    scores, on its declared scale. Two distributions are comparable only on the same scale, so a
    column of labels or raw outputs would raise prediction drift on a model that has not moved.
    """
    pred_col = profile.get("prediction_column")
    if not pred_col:
        return None
    sample = profile.get("sample")
    values = sample.get(pred_col) if isinstance(sample, Mapping) else None
    if not isinstance(values, list):
        return f"the reference profile names prediction column {pred_col!r} but carries no values for it"
    reason = _score_refusal(values)
    if reason:
        return f"the reference profile's prediction column {pred_col!r} is not a baseline: {reason}"
    return None


def build_reference_profile(
    data: Mapping[str, Iterable[float]],
    *,
    prediction_scores: Optional[Iterable[float]] = None,
    prediction_column: str = DEFAULT_PREDICTION_COLUMN,
    max_sample_rows: int = DEFAULT_MAX_SAMPLE_ROWS,
) -> Dict[str, Any]:
    """Build the drift reference profile from training-window columns.

    Args:
        data: mapping feature name -> the feature's training-window values. All columns
            must share the same length (row-aligned). A caller holding a pandas DataFrame
            passes ``df.to_dict("list")``; keeping the input a plain mapping avoids a
            pandas dependency in the capture path.
        prediction_scores: optional per-row model output scores over the training window;
            stored as ``prediction_column`` so the endpoint can measure prediction drift. They
            must be the model's 0-1 anomaly scores as its detector produces them (what
            ``/api/inference/predict`` returns): the endpoint scores the current window that way,
            and two different quantities would differ without anything having drifted. Values
            off that scale, or not finite, are refused (ADR-0021 dec 4).
        prediction_column: name for the prediction column (default ``prediction_score``).
        max_sample_rows: cap on stored rows (default 5000); larger windows are evenly
            down-sampled.

    Returns:
        JSON-serializable profile:
        ``{schema_version, method, columns, prediction_column, n_training_rows,
        n_samples, sample: {col: [values...]}}``.

    Raises:
        ValueError: if ``data`` is empty, columns differ in length, or
            ``prediction_scores`` length does not match the feature rows or holds a value that
            is not a finite score on the detector's declared scale.
    """
    if not data:
        raise ValueError("cannot build a reference profile from no features")
    if max_sample_rows < 1:
        raise ValueError("max_sample_rows must be >= 1")

    columns = list(data.keys())
    materialized = {name: list(values) for name, values in data.items()}

    lengths = {len(v) for v in materialized.values()}
    if len(lengths) != 1:
        raise ValueError(f"all feature columns must share one length, saw {sorted(lengths)}")
    n_rows = lengths.pop()
    if n_rows == 0:
        raise ValueError("feature columns are empty")

    pred_list: Optional[List[float]] = None
    if prediction_scores is not None:
        pred_list = list(prediction_scores)
        if len(pred_list) != n_rows:
            raise ValueError(
                f"prediction_scores length {len(pred_list)} != feature rows {n_rows}"
            )
        reason = _score_refusal(pred_list)
        if reason:
            raise ValueError(f"prediction_scores are not detector scores: {reason}")

    idx = _sample_indices(n_rows, max_sample_rows)

    sample: Dict[str, List[Optional[float]]] = {
        name: _clean_column(materialized[name], idx) for name in columns
    }
    stored_prediction_column: Optional[str] = None
    if pred_list is not None:
        sample[prediction_column] = _clean_column(pred_list, idx)
        stored_prediction_column = prediction_column

    return {
        "schema_version": REFERENCE_PROFILE_VERSION,
        "method": "evidently_sample",
        "columns": columns,
        "prediction_column": stored_prediction_column,
        "n_training_rows": n_rows,
        "n_samples": int(idx.size),
        "sample": sample,
    }
