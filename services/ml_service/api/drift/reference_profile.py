# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The drift baseline's format (ADR-0035).

A baseline has two halves of different origin. The **feature sample** comes from whoever trained the
model, because only they hold what it learned from: a capped, deterministically down-sampled sample of
the training window's columns, in the registration request or, for a notebook-trained model, as a data
artifact of its run (ADR-0040 dec 4). ``build_reference_profile`` is the format's reference
implementation; the notebook client carries a copy held equal by test. The **prediction scores** come
from the platform alone: the deployment gate scores the sample through the model's declared detector in
the serving image (ADR-0035 dec 2) and records them with ``with_derived_scores``. A score column supplied
with a sample is refused.

`evidently` measures drift between a reference and a current dataset, so the sample is stored as
columns of values, compact enough for JSONB. Numeric features only.

The profile also says what state its baseline is in, so a model that cannot be monitored says why
(ADR-0035 dec 4, 5; ADR-0030 dec 8):

* ``refused``: the sample was not accepted at registration; the model has no baseline;
* ``prediction_baseline.origin == "derived"``: scores derived at the last deployment;
* ``prediction_baseline.origin is None``: data drift only, ``reason`` says why;
* a ``prediction_column`` and no ``prediction_baseline``: scores supplied before ADR-0035, kept until
  the model's next deployment (ADR-0035 dec 7).
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

# Cap on stored reference rows (ADR-0035 dec 8), on every registration path. Evidently's tests are
# stable well below this; the cap bounds the stored payload and the deployment gate's scoring of it
# whatever the training-window size. The notebook client builds its sample to the same cap.
DEFAULT_MAX_SAMPLE_ROWS = 5000

# The column the derived scores are stored under.
DEFAULT_PREDICTION_COLUMN = "prediction_score"

# Where a notebook-trained model's feature sample lives in its run (ADR-0040 dec 4); the notebook client
# writes the same name. Read at promotion only up to this size bound (ADR-0035 dec 8): the row cap of a
# few dozen inputs is well under it, and a larger file is refused unread.
FEATURE_SAMPLE_ARTIFACT = "industryflow/feature_sample.json"
MAX_SAMPLE_BYTES = 16 * 1024 * 1024


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

    ADR-0021 dec 1: the column holds what the current side holds — the declared detector's
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
    max_sample_rows: int = DEFAULT_MAX_SAMPLE_ROWS,
) -> Dict[str, Any]:
    """Build a feature sample from training-window columns.

    Args:
        data: mapping feature name -> the feature's training-window values, row-aligned. Its names are
            the inputs of the feature configuration the model will be bound to (ADR-0035 dec 4). A
            caller holding a pandas DataFrame passes ``df.to_dict("list")``.
        max_sample_rows: cap on stored rows (default 5000); larger windows are evenly down-sampled.

    Returns:
        JSON-serializable ``{schema_version, method, columns, prediction_column, n_training_rows,
        n_samples, sample: {col: [values...]}}``; ``prediction_column`` is ``None`` until the platform
        derives scores.

    Raises:
        ValueError: if ``data`` is empty or its columns differ in length.
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

    idx = _sample_indices(n_rows, max_sample_rows)
    return {
        "schema_version": REFERENCE_PROFILE_VERSION,
        "method": "evidently_sample",
        "columns": columns,
        "prediction_column": None,
        "n_training_rows": n_rows,
        "n_samples": int(idx.size),
        "sample": {name: _clean_column(materialized[name], idx) for name in columns},
    }


# --- what the platform accepts and records (ADR-0035) ----------------------------------------------

def supplied_scores_refusal(profile: Mapping[str, Any]) -> Optional[str]:
    """Why a sample offered at registration carries scores the platform must derive itself, or ``None``.

    ADR-0035 dec 2: a score column supplied with the sample is not accepted. The platform can check only
    that a supplied score is in range, which cannot tell the declared detector's scores from another
    quantity on the same scale.
    """
    if profile.get("prediction_column") or profile.get("prediction_baseline"):
        return ("the sample carries prediction scores; the platform derives them from the sample "
                "through the model's detector when it is deployed (ADR-0035 dec 2)")
    return None


def sample_refusal(profile: Any, base_sensors: Sequence[str]) -> Optional[str]:
    """Why ``profile`` is not a feature sample for a model bound to ``base_sensors``, or ``None``.

    ADR-0035 dec 4: its columns are exactly the inputs of the bound feature configuration. Each column
    holds the same number of values, every value a finite number or null.
    """
    if not isinstance(profile, Mapping):
        return "the sample is not an object"
    if profile.get("schema_version") != REFERENCE_PROFILE_VERSION:
        return (f"the sample's schema_version is {profile.get('schema_version')!r}, "
                f"not {REFERENCE_PROFILE_VERSION}")
    columns, sample = profile.get("columns"), profile.get("sample")
    if not isinstance(columns, list) or not isinstance(sample, Mapping):
        return "the sample has no columns and values"
    if not base_sensors:
        return "the model is bound to no feature configuration with base sensors, so a sample matches nothing"
    if sorted(map(str, columns)) != sorted(base_sensors) or len(set(columns)) != len(columns):
        return (f"the sample's columns {sorted(map(str, columns))} are not the feature "
                f"configuration's inputs {sorted(base_sensors)}")
    lengths = set()
    for name in columns:
        values = sample.get(name)
        if not isinstance(values, list) or not values:
            return f"the sample holds no values for {name!r}"
        for v in values:
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                return f"the sample's column {name!r} holds {v!r}, which is not a number"
        lengths.add(len(values))
    if len(lengths) != 1:
        return f"the sample's columns differ in length: {sorted(lengths)}"
    return None


def accepted_sample(profile: Mapping[str, Any]) -> Dict[str, Any]:
    """The sample as stored: its own fields, nothing a supplier added beside them, and at most
    ``DEFAULT_MAX_SAMPLE_ROWS`` rows on every registration path (ADR-0035 dec 8), evenly down-sampled
    as the notebook client does."""
    columns = list(profile["columns"])
    supplied = len(profile["sample"][columns[0]])
    idx = _sample_indices(supplied, DEFAULT_MAX_SAMPLE_ROWS)
    sample = {c: [profile["sample"][c][i] for i in idx] for c in columns}
    n = len(idx)
    n_training = profile.get("n_training_rows")
    if isinstance(n_training, bool) or not isinstance(n_training, int) or n_training < supplied:
        n_training = supplied
    return {
        "schema_version": REFERENCE_PROFILE_VERSION,
        "method": "evidently_sample",
        "columns": columns,
        "prediction_column": None,
        "n_training_rows": n_training,
        "n_samples": n,
        "sample": sample,
    }


def refused_baseline(reason: str) -> Dict[str, Any]:
    """What a model whose sample was not accepted stores: no baseline, and why (ADR-0035 dec 4)."""
    return {"schema_version": REFERENCE_PROFILE_VERSION, "refused": reason}


def sample_rows(profile: Mapping[str, Any]) -> List[Dict[str, float]]:
    """The sample's rows, each a mapping of input to value; a row with a missing value is skipped,
    because a fill value would be a reading nobody took."""
    columns = list(profile.get("columns") or [])
    sample = profile.get("sample") or {}
    if not columns:
        return []
    n = len(sample.get(columns[0]) or [])
    rows = []
    for i in range(n):
        row = {c: sample[c][i] for c in columns}
        if all(v is not None for v in row.values()):
            rows.append({c: float(v) for c, v in row.items()})
    return rows


def _features_only(profile: Mapping[str, Any]) -> Dict[str, Any]:
    out = {k: v for k, v in profile.items() if k not in ("prediction_baseline",)}
    pred = profile.get("prediction_column")
    out["sample"] = {k: v for k, v in (profile.get("sample") or {}).items() if k != pred}
    out["prediction_column"] = None
    return out


def with_derived_scores(profile: Mapping[str, Any], scores: Sequence[float], *, derived_at: str) -> Dict[str, Any]:
    """The profile with the scores the deployment gate derived from its sample (ADR-0035 dec 2, 3).

    The scores are their own sample of the detector's output on the sample's complete rows, so they
    need not match a column's length. Raises ValueError for values off the declared scale.
    """
    reason = _score_refusal(list(scores))
    if reason:
        raise ValueError(f"derived scores are not detector scores: {reason}")
    out = _features_only(profile)
    out["prediction_column"] = DEFAULT_PREDICTION_COLUMN
    out["sample"][DEFAULT_PREDICTION_COLUMN] = [float(v) for v in scores]
    out["prediction_baseline"] = {"origin": "derived", "derived_at": derived_at}
    return out


def without_prediction_baseline(profile: Mapping[str, Any], reason: str) -> Dict[str, Any]:
    """The profile with no prediction baseline, saying why; data drift still runs (ADR-0035 dec 5)."""
    out = _features_only(profile)
    out["prediction_baseline"] = {"origin": None, "reason": reason}
    return out


def baseline_state(profile: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    """What a model's baseline lets drift monitoring do, and why not where it cannot."""
    if not profile:
        return {"data_drift": False, "prediction": None,
                "reason": "no feature sample was supplied, so the model cannot be monitored for drift"}
    if profile.get("refused"):
        return {"data_drift": False, "prediction": None, "reason": profile["refused"]}
    pb = profile.get("prediction_baseline")
    if isinstance(pb, Mapping):
        if pb.get("origin") == "derived":
            return {"data_drift": True, "prediction": "derived", "derived_at": pb.get("derived_at"),
                    "reason": None}
        return {"data_drift": True, "prediction": None, "reason": pb.get("reason")}
    if profile.get("prediction_column"):
        return {"data_drift": True, "prediction": "supplied",
                "reason": "scores supplied before ADR-0035; derived at the model's next deployment"}
    return {"data_drift": True, "prediction": None,
            "reason": "prediction scores are derived when the model is deployed"}
