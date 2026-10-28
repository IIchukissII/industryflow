# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The feature sample a model's drift baseline is built from (ADR-0035, ADR-0040 dec 4).

Drift monitoring compares a model's live inputs with what it learned from, and only the notebook that
trained it holds that. Log a sample of the training inputs into the run that logs the model:

    with mlflow.start_run():
        mlflow.sklearn.log_model(model, name="model", registered_model_name="vpd-regressor")
        log_feature_sample(train[["temp", "humidity"]])

The columns are the base sensors of the feature configuration the model will be promoted with; the
platform refuses a sample whose columns differ. Log the inputs only: the platform scores the sample
through the model's detector itself when the model is deployed.

The format is the serving side's (`services/ml_service/api/drift/reference_profile.py`); a test holds
this copy to it.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Mapping, Optional, Sequence

import numpy as np

# Where the platform looks for the sample in a run.
FEATURE_SAMPLE_ARTIFACT = "industryflow/feature_sample.json"

REFERENCE_PROFILE_VERSION = 2
DEFAULT_MAX_SAMPLE_ROWS = 5000


def _sample_indices(n_rows: int, max_rows: int) -> np.ndarray:
    if n_rows <= max_rows:
        return np.arange(n_rows)
    return np.linspace(0, n_rows - 1, max_rows).astype(int)


def _clean_column(values: Sequence[float], idx: np.ndarray) -> List[Optional[float]]:
    arr = np.asarray(values, dtype="float64")[idx]
    return [float(v) if math.isfinite(v) else None for v in arr]


def feature_sample(data: Any, *, max_sample_rows: int = DEFAULT_MAX_SAMPLE_ROWS) -> Dict[str, Any]:
    """A feature sample of ``data``: a pandas DataFrame, or a mapping of input name to row-aligned
    values. Longer data is evenly down-sampled to ``max_sample_rows``; NaN and infinities become null."""
    if hasattr(data, "to_dict") and hasattr(data, "columns"):
        data = {str(c): data[c].tolist() for c in data.columns}
    if not isinstance(data, Mapping) or not data:
        raise ValueError("cannot build a feature sample from no inputs")
    if max_sample_rows < 1:
        raise ValueError("max_sample_rows must be >= 1")

    columns = list(data.keys())
    materialized: Dict[str, List[Any]] = {name: list(values) for name, values in data.items()}
    lengths = {len(v) for v in materialized.values()}
    if len(lengths) != 1:
        raise ValueError(f"all input columns must share one length, saw {sorted(lengths)}")
    n_rows = lengths.pop()
    if n_rows == 0:
        raise ValueError("input columns are empty")

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


def log_feature_sample(data: Any, *, max_sample_rows: int = DEFAULT_MAX_SAMPLE_ROWS) -> Dict[str, Any]:
    """Log ``feature_sample(data)`` into the active MLflow run, where promotion reads it; returns it."""
    import mlflow

    if mlflow.active_run() is None:
        raise RuntimeError("no active MLflow run: log the sample inside the run that logs the model")
    sample = feature_sample(data, max_sample_rows=max_sample_rows)
    mlflow.log_dict(sample, FEATURE_SAMPLE_ARTIFACT)
    return sample
