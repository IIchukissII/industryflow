# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Drift scoring via evidently (ADR-0021 decisions 1 + 3).

Given the stored reference profile (an evidently-consumable sample; see
:mod:`drift.reference_profile`) and a recent window of feature values, measure how far the
recent distribution has moved from the training baseline using ``evidently``'s data-drift
tests. Both signals are label-free, so v1 needs no ground truth:

- **data drift** — the input-feature columns of the current window vs the reference sample;
- **prediction drift** — the model's own output-score column (if the reference captured
  one) vs its training-window distribution, scored through the identical path.

This module computes the *scores*; the alert service applies each rule's threshold and
decides whether to fire (ADR-0021 decision 3). The primary scalar is ``drift_share`` — the
fraction of feature columns evidently flags as drifted — matching the ADR's
"input drift 0.42 > 0.30" framing.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import pandas as pd
from evidently import DataDefinition, Dataset, Report
from evidently.presets import DataDriftPreset

from .reference_profile import REFERENCE_PROFILE_VERSION

# Default advisory threshold on the drifted-feature share; the authoritative threshold is
# the alert rule's (Slice 3).
DEFAULT_DRIFT_SHARE_THRESHOLD = 0.5

# evidently needs a minimum of data on each side for its statistical tests to mean
# anything; below this we report "insufficient data" rather than a noisy verdict.
MIN_ROWS = 30

_DRIFTED_COUNT_TYPE = "DriftedColumnsCount"
_VALUE_DRIFT_TYPE = "ValueDrift"


def _is_drifted(method: Optional[str], value: Any, threshold: Optional[float]) -> Optional[bool]:
    """Direction of an evidently ValueDrift verdict depends on the test family.

    p-value tests (e.g. K-S) flag drift when the statistic falls *below* the threshold;
    distance/divergence tests (Wasserstein, PSI, Jensen-Shannon) flag it when the distance
    rises *at or above* the threshold.
    """
    if value is None or threshold is None:
        return None
    if "p_value" in (method or ""):
        return bool(value < threshold)
    return bool(value >= threshold)


def _run_report(
    ref_df: pd.DataFrame,
    cur_df: pd.DataFrame,
    columns: List[str],
) -> Tuple[Optional[float], Optional[int], Dict[str, Any]]:
    """Run evidently's data-drift preset over ``columns``; parse the snapshot dict.

    Returns ``(drift_share, drifted_count, per_column)``.
    """
    data_def = DataDefinition(numerical_columns=columns)
    ref_ds = Dataset.from_pandas(ref_df[columns], data_definition=data_def)
    cur_ds = Dataset.from_pandas(cur_df[columns], data_definition=data_def)

    snapshot = Report(metrics=[DataDriftPreset()]).run(
        current_data=cur_ds, reference_data=ref_ds
    )
    result = snapshot.dict()

    share: Optional[float] = None
    count: Optional[int] = None
    per_column: Dict[str, Any] = {}

    for metric in result.get("metrics", []):
        config = metric.get("config", {})
        mtype = config.get("type", "")
        value = metric.get("value")

        if mtype.endswith(_DRIFTED_COUNT_TYPE) and isinstance(value, dict):
            share = value.get("share")
            count = int(value["count"]) if value.get("count") is not None else None
        elif mtype.endswith(_VALUE_DRIFT_TYPE):
            col = config.get("column")
            method = config.get("method")
            threshold = config.get("threshold")
            per_column[col] = {
                "method": method,
                "threshold": threshold,
                "value": value,
                "drifted": _is_drifted(method, value, threshold),
            }

    return share, count, per_column


def compute_drift(
    reference_profile: Mapping[str, Any],
    current_data: Mapping[str, Iterable[float]],
    *,
    drift_share_threshold: float = DEFAULT_DRIFT_SHARE_THRESHOLD,
) -> Dict[str, Any]:
    """Score data + prediction drift of a recent window against the reference profile.

    Args:
        reference_profile: the stored profile (schema v2). Callers must handle a *missing*
            profile ("drift unavailable") before calling this.
        current_data: mapping column -> recent-window values. Feature columns are matched
            against the reference by name; if the reference captured a prediction column
            and ``current_data`` supplies it (the endpoint scores the window with the warm
            model), prediction drift is reported too.
        drift_share_threshold: advisory threshold on the drifted-feature share for the
            convenience ``drift_detected`` flag. The alert service supplies the
            authoritative one (ADR-0021 dec 3).

    Returns:
        ``{status, schema_version, threshold, n_reference_rows, n_current_rows,
        data_drift: {drift_share, n_drifted, n_columns, drift_detected, per_column},
        prediction_drift: {column, value, method, drifted} | None,
        unmatched_features}``. ``status`` is ``"unavailable"`` when no feature columns
        overlap and ``"insufficient_data"`` when either side is below ``MIN_ROWS``.

    Raises:
        ValueError: if the profile schema version is not understood.
    """
    version = reference_profile.get("schema_version")
    if version != REFERENCE_PROFILE_VERSION:
        raise ValueError(
            f"unsupported reference_profile schema_version {version!r} "
            f"(expected {REFERENCE_PROFILE_VERSION})"
        )

    ref_df = pd.DataFrame(reference_profile.get("sample", {}))
    cur_df = pd.DataFrame({k: list(v) for k, v in current_data.items()})

    feature_cols = [c for c in reference_profile.get("columns", []) if c in cur_df.columns]
    unmatched = [c for c in reference_profile.get("columns", []) if c not in cur_df.columns]
    pred_col = reference_profile.get("prediction_column")

    base = {
        "schema_version": version,
        "threshold": drift_share_threshold,
        "n_reference_rows": int(len(ref_df)),
        "n_current_rows": int(len(cur_df)),
        "unmatched_features": unmatched,
    }

    if not feature_cols:
        return {**base, "status": "unavailable",
                "reason": "no overlapping feature columns between reference and current window"}

    if len(ref_df) < MIN_ROWS or len(cur_df) < MIN_ROWS:
        return {**base, "status": "insufficient_data",
                "reason": f"need >= {MIN_ROWS} rows on both sides"}

    share, count, per_column = _run_report(ref_df, cur_df, feature_cols)
    n_cols = len(feature_cols)
    data_drift = {
        "drift_share": share,
        "n_drifted": count,
        "n_columns": n_cols,
        "drift_detected": bool(share is not None and share > drift_share_threshold),
        "per_column": per_column,
    }

    prediction_drift = None
    if pred_col and pred_col in cur_df.columns and pred_col in ref_df.columns:
        _, _, pred_per_col = _run_report(ref_df, cur_df, [pred_col])
        pc = pred_per_col.get(pred_col, {})
        prediction_drift = {
            "column": pred_col,
            "value": pc.get("value"),
            "method": pc.get("method"),
            "threshold": pc.get("threshold"),
            "drifted": pc.get("drifted"),
        }

    return {**base, "status": "ok",
            "data_drift": data_drift, "prediction_drift": prediction_drift}
