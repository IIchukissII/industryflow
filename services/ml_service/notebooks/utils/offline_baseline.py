# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Offline re-derivation of the serving baseline, for training data (ADR-0023 dec 9).

At serve time a ``statistical`` feature reads the most recent **closed** window of the
Spark-materialized aggregates (``sensor_aggregations_<granularity>``) and returns a statistic of
it. Training on historical data (a CSV, a cold-layer Parquet export) cannot issue that read — it
must re-derive the same quantity offline. "Same" is the whole point: a training feature computed
any other way is train/serve skew, which is what this module exists to prevent.

The re-derivation, per row at time ``t``:

    closed(t)   = floor((t - L) / W) - 1    — W = the granularity's width, L = its allowed lateness
                                              (the aggregation watermark delay), both in seconds
    baseline(t) = statistic over the newest window <= closed(t) that holds samples

``closed(t)`` is the newest window whose row the Spark job has written by time ``t``. It emits a
window once (append mode, ADR-0006 dec 2), when the watermark — the latest event time minus ``L`` —
passes the window's end; so for 1-minute windows with 2 minutes of lateness, serving reads
``window(t) - 3``, not ``window(t) - 1``. W and L come from the one window contract
(``industryflow_contracts.windows``, ADR-0006 dec 7) that the Spark job and the serving provider read
too, so the three cannot hold different numbers.

It is therefore **causal**: a row is described by samples that strictly precede its own window,
never by its own window (still open at serve time) and never by the future. A mean over a whole
history (``groupby(...).transform('mean')``) is neither — it averages samples taken *after* the row
it describes, so inference cannot reproduce it at any window size, and a model trained on it is fed
a feature it never sees in production. That is the defect this replaces (issue #173).

Rows with no written window yet (the start of a history) get the neutral ``0.0`` the serving
transform degrades to when no aggregate row exists, so the two agree on cold start too.
"""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd
from industryflow_contracts import windows

# Window width and allowed lateness (watermark delay) per granularity, read from the window contract
# (ADR-0006 dec 7): views of it for callers that index by granularity, never a second table.
GRANULARITY_SECONDS: Dict[str, int] = {g: w.width_seconds for g, w in windows.WINDOWS.items()}
WATERMARK_SECONDS: Dict[str, int] = {g: w.lateness_seconds for g, w in windows.WINDOWS.items()}
DEFAULT_GRANULARITY = windows.DEFAULT_GRANULARITY

# stat_type -> (feature-name suffix, aggregate to read)
_STATS = {
    "deviation_from_window_mean": ("_deviation", "mean"),
    "window_mean": ("_window_mean", "mean"),
    "window_std": ("_window_std", "std"),
}


def _epoch_seconds(timestamps: pd.Series) -> pd.Series:
    # Convert to seconds by *casting the resolution*, not by dividing raw integers: the integer
    # backing a datetime64 is nanoseconds on pandas 2 but can be milliseconds or microseconds on
    # pandas 3, so a hardcoded 1e9 divisor silently buckets every row into one window there.
    return pd.to_datetime(timestamps).astype("datetime64[s]").astype("int64")


def window_index(timestamps: pd.Series, granularity: str = DEFAULT_GRANULARITY) -> pd.Series:
    """Tumbling-window index of each timestamp — the same bucketing Spark's windowing applies."""
    return _epoch_seconds(timestamps) // GRANULARITY_SECONDS[granularity]


def latest_written_window(timestamps: pd.Series, granularity: str = DEFAULT_GRANULARITY) -> pd.Series:
    """Index of the newest window whose aggregate row exists at each timestamp: ``closed(t)``."""
    return windows.latest_emitted_window(_epoch_seconds(timestamps), granularity)


def add_window_features(
    df: pd.DataFrame,
    sensor_cols: Sequence[str],
    *,
    granularity: str = DEFAULT_GRANULARITY,
    stats: Iterable[str] = ("deviation_from_window_mean",),
    timestamp_col: str = "timestamp",
    group_cols: Sequence[str] = (),
) -> Tuple[pd.DataFrame, List[dict]]:
    """Add causal closed-window features and the feature config that serves them.

    Returns ``(df, transformations)`` — the frame with one column per (sensor, stat), and the
    matching ``transformations`` entries. Both come from this one call so the column a model trains
    on and the config that serves it cannot describe different computations.

    ``group_cols`` scopes the windowing the way the serving key does. At serve time the aggregate is
    keyed by ``(equipment, sensor)``, so a frame holding **more than one equipment's history** must
    group by whatever identifies them (e.g. ``["equipment_id"]``) — otherwise one equipment's tail
    leaks into another's first window and the offline feature stops matching what serving computes.
    The default (no grouping) is the single continuous history: one equipment, one timeline.
    """
    if granularity not in GRANULARITY_SECONDS:
        raise ValueError(
            f"unknown granularity {granularity!r}; expected one of {sorted(GRANULARITY_SECONDS)}"
        )
    unknown = set(stats) - set(_STATS)
    if unknown:
        raise ValueError(f"unknown stat_type(s) {sorted(unknown)}; expected {sorted(_STATS)}")
    if timestamp_col not in df.columns:
        raise ValueError(f"timestamp column {timestamp_col!r} not in frame")

    group_cols = list(group_cols)
    missing = [c for c in group_cols if c not in df.columns]
    if missing:
        raise ValueError(f"group column(s) {missing} not in frame")

    # The row order the caller hands us is not necessarily time order — training frames are often
    # shuffled before engineering — and a causal computation is meaningless on shuffled rows. Sort a
    # copy, compute, then restore the caller's original order so downstream joins/labels still line up.
    original_index = df.index
    df = df.sort_values(group_cols + [timestamp_col])
    df["_window"] = window_index(df[timestamp_col], granularity)
    df["_closed"] = latest_written_window(df[timestamp_col], granularity)

    transformations: List[dict] = []
    for sensor in sensor_cols:
        # The statistic of each window, matched to each row as the newest window at or before the
        # row's closed(t) — the row the serving provider reads: the latest one written (a window with
        # no samples is never written, so the one before it is). merge_asof needs both sides sorted
        # on the key; `by` keeps each equipment's windows to itself.
        per_window = (df.groupby(group_cols + ["_window"])[sensor].agg(["mean", "std"])
                      .reset_index().sort_values("_window"))
        rows = df[group_cols + ["_closed"]].reset_index(drop=True)
        rows["_pos"] = range(len(rows))
        aligned = pd.merge_asof(
            rows.sort_values("_closed"), per_window, left_on="_closed", right_on="_window",
            by=group_cols or None, direction="backward",
        ).sort_values("_pos")  # back to df's (sorted) row order, positionally

        for stat_type in stats:
            suffix, aggregate = _STATS[stat_type]
            baseline = pd.Series(aligned[aggregate].to_numpy(), index=df.index)
            if stat_type == "deviation_from_window_mean":
                column = df[sensor] - baseline
            else:
                column = baseline
            # No written window yet (the start of a history), or a single-sample window with no
            # stddev: the neutral value the serving transform degrades to.
            df[f"{sensor}{suffix}"] = column.fillna(0.0)
            transformations.append(feature_config(sensor, stat_type, granularity))

    df = df.drop(columns=["_window", "_closed"]).reindex(original_index)
    return df, transformations


def feature_config(sensor: str, stat_type: str, granularity: str = DEFAULT_GRANULARITY,
                   name: Optional[str] = None) -> dict:
    """The ``transformations`` entry that makes serving compute what ``add_window_features`` did."""
    suffix, _ = _STATS[stat_type]
    return {
        "name": name or f"{sensor}{suffix}",
        "type": "statistical",
        "sensor": sensor,
        "params": {"stat_type": stat_type, "granularity": granularity},
    }
