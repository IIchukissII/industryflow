# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Offline re-derivation of the serving baseline, for training data (ADR-0023 rev 2 dec 9).

At serve time a ``statistical`` feature reads the most recent **closed** window of the
Spark-materialized aggregates (``sensor_aggregations_<granularity>``) and returns a statistic of
it. Training data for the reference models is a static CSV, so it cannot issue that read — it must
re-derive the same quantity offline. "Same" is the whole point: a training feature computed any
other way is train/serve skew, which is what this module exists to prevent.

The re-derivation, per row at time ``t``:

    window(t)   = floor(t / W)                     — W = the granularity's width in seconds
    baseline(t) = statistic over window(t) - 1     — the most recent *closed* window

It is therefore **causal**: a row is described by samples that strictly precede its own window,
never by its own window (still open at serve time) and never by the future. The whole-run mean this
replaces (``groupby('simulationRun').transform('mean')``) was neither causal nor reproducible at
serve time — it averaged every sample of the run, including ones after the row being described.

Rows in a run's first window have no closed predecessor and get the neutral ``0.0`` the serving
transform degrades to when no aggregate row exists yet, so the two agree on the cold-start case too.
"""
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import pandas as pd

# Window width per granularity. Must equal the serving side's GRANULARITY_SECONDS
# (services/ml_service/api/feature_engineering/baseline_provider.py) — a test asserts the two maps
# are identical, so a granularity added on one side and not the other fails CI rather than silently
# reintroducing skew.
GRANULARITY_SECONDS: Dict[str, int] = {
    "1min": 60,
    "5min": 300,
    "1hour": 3600,
}
DEFAULT_GRANULARITY = "1min"

# stat_type -> (feature-name suffix, aggregate to read)
_STATS = {
    "deviation_from_window_mean": ("_deviation", "mean"),
    "window_mean": ("_window_mean", "mean"),
    "window_std": ("_window_std", "std"),
}


def window_index(timestamps: pd.Series, granularity: str = DEFAULT_GRANULARITY) -> pd.Series:
    """Tumbling-window index of each timestamp — the same bucketing Spark's windowing applies."""
    width = GRANULARITY_SECONDS[granularity]
    # Convert to seconds by *casting the resolution*, not by dividing raw integers: the integer
    # backing a datetime64 is nanoseconds on pandas 2 but can be milliseconds or microseconds on
    # pandas 3, so a hardcoded 1e9 divisor silently buckets every row into one window there.
    epoch_seconds = pd.to_datetime(timestamps).astype("datetime64[s]").astype("int64")
    return epoch_seconds // width


def add_window_features(
    df: pd.DataFrame,
    sensor_cols: Sequence[str],
    *,
    granularity: str = DEFAULT_GRANULARITY,
    stats: Iterable[str] = ("deviation_from_window_mean",),
    timestamp_col: str = "timestamp",
    group_cols: Sequence[str] = ("simulationRun",),
) -> Tuple[pd.DataFrame, List[dict]]:
    """Add causal closed-window features and the feature config that serves them.

    Returns ``(df, transformations)`` — the frame with one column per (sensor, stat), and the
    matching ``transformations`` entries. Both come from this one call so the column a model trains
    on and the config that serves it cannot describe different computations.

    ``group_cols`` scopes the windowing the way the serving key does. At serve time the aggregate is
    keyed by ``(equipment, sensor)``; in the TEP CSV each ``simulationRun`` is an independent
    equipment history (they are offset by a day apiece), so grouping by run is the offline
    equivalent — it stops one run's tail from leaking into the next run's first window.
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
    # The row order the caller hands us is not necessarily time order — the TEP notebooks shuffle
    # before engineering — and a causal computation is meaningless on shuffled rows. Sort a copy,
    # compute, then restore the caller's original order so downstream joins/labels still line up.
    original_index = df.index
    df = df.sort_values(group_cols + [timestamp_col])
    df["_window"] = window_index(df[timestamp_col], granularity)

    transformations: List[dict] = []
    for sensor in sensor_cols:
        # The statistic of each closed window, then shifted onto the rows of the NEXT window: a row
        # reads the window before its own, which is exactly the "most recent closed window" the
        # serving provider selects.
        per_window = df.groupby(group_cols + ["_window"])[sensor].agg(["mean", "std"])
        closed = per_window.groupby(group_cols).shift(1)
        aligned = df[group_cols + ["_window"]].merge(
            closed, left_on=group_cols + ["_window"], right_index=True, how="left",
        )

        for stat_type in stats:
            suffix, aggregate = _STATS[stat_type]
            baseline = pd.Series(aligned[aggregate].to_numpy(), index=df.index)
            if stat_type == "deviation_from_window_mean":
                column = df[sensor] - baseline
            else:
                column = baseline
            # No closed predecessor (a run's first window), or a single-sample window with no
            # stddev: the neutral value the serving transform degrades to.
            df[f"{sensor}{suffix}"] = column.fillna(0.0)
            transformations.append(feature_config(sensor, stat_type, granularity))

    df = df.drop(columns="_window").reindex(original_index)
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
