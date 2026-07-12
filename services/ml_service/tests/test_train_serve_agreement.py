# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The train/serve agreement proof for the ``statistical`` feature (ADR-0023 rev 2 dec 9, issue #173).

Training re-derives the windowed baseline offline (``notebooks/utils/offline_baseline``) because
historical training data cannot read the tenant's aggregate table; inference reads that table
through ``AggregateBaselineProvider``. Two computations, one quantity — which holds only as long as
something checks. That is these tests: the offline column and the value the *actual* serving
transform produces must be the same number for the same data, and the two sides' window definitions
must not drift apart.

The defect this guards (issue #173): training computed a mean over a whole history
(``groupby(...).transform('mean')``), which averages samples taken *after* the row it describes,
while serving computed a deviation from a recent closed window. The model was fed a feature it had
never seen.
"""
import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))
# The module directly, not through `utils/__init__` — the package pulls in the config builder's
# `requests` dependency, which this test has no use for.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'notebooks', 'utils'))

from extensions import get_transform, TransformContext  # noqa: E402
from feature_engineering import baseline_provider  # noqa: E402
import offline_baseline  # noqa: E402

STATISTICAL = get_transform("statistical")
EQUIPMENT_ID = "550e8400-e29b-41d4-a716-446655440100"
COMPANY_ID = "11111111-2222-3333-4444-555555555555"


# The serving aggregate is keyed by (equipment, sensor), so a training frame holding several
# equipments must window per equipment — that is what GROUP_COLS expresses.
GROUP_COLS = ["equipment_id"]


def _frame(n_samples=150, interval_seconds=1, equipments=("eq-1",)):
    """A training frame shaped like history: one row per (equipment, sample), timestamps at a fixed
    interval, each equipment on its own day — and shuffled, as training frames usually arrive."""
    rows = []
    for n, equipment in enumerate(equipments):
        base = pd.Timestamp("2026-01-01") + pd.Timedelta(days=n)
        for i in range(n_samples):
            rows.append({
                "equipment_id": equipment,
                "sample": i + 1,
                "timestamp": base + pd.Timedelta(seconds=i * interval_seconds),
                # A value that varies within and across windows, so a wrong window is a wrong number.
                "xmeas_1": 70.0 + i * 0.1 + n + 1,
            })
    return pd.DataFrame(rows).sample(frac=1, random_state=7).reset_index(drop=True)


class _ProviderOver:
    """The serving provider, answering from the same data the offline re-derivation saw.

    It does what ``AggregateBaselineProvider`` does — return the most recent *closed* window's
    aggregate — but sourced from the frame instead of the aggregate table, so the test compares the
    two *computations* rather than re-testing the SQL. The window it serves is chosen by `now`: the
    caller sets the current time, and the provider answers with the window before it.
    """

    def __init__(self, df, now, granularity="1min", sensor="xmeas_1", equipment="eq-1"):
        width = offline_baseline.GRANULARITY_SECONDS[granularity]
        current_window = int(now.timestamp()) // width
        window = df[(df["equipment_id"] == equipment)
                    & (offline_baseline.window_index(df["timestamp"], granularity)
                       == current_window - 1)]
        self.baseline = None if window.empty else {
            "mean": float(window[sensor].mean()),
            "std": None if len(window) < 2 else float(window[sensor].std()),
        }

    async def compute_baseline(self, company_id, equipment_id, sensor_name, granularity=None):
        return self.baseline


def _ctx(provider):
    return TransformContext(baseline_provider=provider, equipment_id=EQUIPMENT_ID,
                            company_id=COMPANY_ID)


# ------------------------------------------------------------------ the definitions cannot drift

def test_granularities_agree_between_training_and_serving():
    """Both sides key their window off the same names, with the same widths.

    A granularity added to one side and not the other would let a config name a window that only one
    side understands — skew reintroduced by omission. This fails CI instead.
    """
    assert set(offline_baseline.GRANULARITY_SECONDS) == set(baseline_provider._AGG_TABLES)
    assert offline_baseline.GRANULARITY_SECONDS == baseline_provider.GRANULARITY_SECONDS
    assert offline_baseline.DEFAULT_GRANULARITY == baseline_provider.DEFAULT_GRANULARITY


def test_window_index_buckets_by_wall_clock_seconds():
    """A 1-min window must hold 60 one-second samples — not the whole frame.

    The integer backing a datetime64 is nanoseconds on pandas 2 but coarser on pandas 3, so a
    hardcoded ns divisor collapses every row into a single window. That yields an all-neutral
    feature and an agreement test that passes vacuously, which is how it nearly slipped through.
    """
    timestamps = pd.Series(pd.date_range("2026-01-01", periods=150, freq="1s"))
    windows = offline_baseline.window_index(timestamps, "1min")

    assert windows.nunique() == 3                       # 150s spans three 1-min windows
    assert (windows.value_counts().sort_index().tolist()) == [60, 60, 30]
    assert offline_baseline.window_index(timestamps, "5min").nunique() == 1


def test_emitted_config_names_a_stat_type_serving_implements():
    """The training helper must not emit a stat_type the serving transform would neutralize."""
    from extensions import builtins  # noqa: PLC0415

    for stat_type in ("deviation_from_window_mean", "window_mean", "window_std"):
        config = offline_baseline.feature_config("xmeas_1", stat_type)
        assert config["params"]["stat_type"] in builtins._STAT_TYPES
        assert config["params"]["granularity"] in baseline_provider._AGG_TABLES
        assert config["type"] == "statistical"


# ------------------------------------------------------------------------ the numbers must agree

@pytest.mark.asyncio
@pytest.mark.parametrize("stat_type,suffix", [
    ("deviation_from_window_mean", "_deviation"),
    ("window_mean", "_window_mean"),
    ("window_std", "_window_std"),
])
async def test_offline_feature_equals_what_serving_computes(stat_type, suffix):
    """The heart of it: same row, same data — the offline column and the live transform agree."""
    df = _frame()
    engineered, _ = offline_baseline.add_window_features(
        df.copy(), ["xmeas_1"], granularity="1min", stats=(stat_type,),
    )
    transformation = offline_baseline.feature_config("xmeas_1", stat_type)

    # Agreement on a column of zeros is agreement on nothing: if the windowing collapsed (every row
    # in one bucket, so every baseline is the cold-start neutral), both sides would return 0.0 and
    # this test would pass while proving nothing. Demand a live signal before believing the match.
    feature = engineered[f"xmeas_1{suffix}"]
    assert (feature != 0.0).sum() > len(df) // 2, "windowing collapsed — the comparison is vacuous"
    assert feature.round(6).nunique() > 1

    checked = 0
    for _, row in engineered.iterrows():
        # Serve this row as if it had just arrived: the provider answers with the window closed
        # before the row's own timestamp, which is what inference would read.
        provider = _ProviderOver(df, now=row["timestamp"])
        served = await STATISTICAL(transformation, {"xmeas_1": row["xmeas_1"]}, _ctx(provider))
        assert served == pytest.approx(row[f"xmeas_1{suffix}"]), (
            f"train/serve skew at sample {row['sample']}: "
            f"offline={row[f'xmeas_1{suffix}']} served={served}"
        )
        checked += 1
    assert checked == len(df)


@pytest.mark.asyncio
async def test_cold_start_rows_agree_on_neutral():
    """A history's first window has no closed predecessor. Training must record the same neutral the
    serving transform degrades to, or the model learns a value inference never produces."""
    df = _frame(n_samples=90)
    engineered, _ = offline_baseline.add_window_features(df.copy(), ["xmeas_1"])
    first_window = engineered[offline_baseline.window_index(engineered["timestamp"]) ==
                              offline_baseline.window_index(engineered["timestamp"]).min()]

    assert not first_window.empty
    assert (first_window["xmeas_1_deviation"] == 0.0).all()

    row = first_window.iloc[0]
    served = await STATISTICAL(
        offline_baseline.feature_config("xmeas_1", "deviation_from_window_mean"),
        {"xmeas_1": row["xmeas_1"]}, _ctx(_ProviderOver(df, now=row["timestamp"])),
    )
    assert served == 0.0


# ------------------------------------------------------------- the offline derivation is causal

def test_offline_feature_is_causal():
    """No row may be described by its own window or the future.

    Mutating a *later* sample must not change an earlier row's feature. The whole-history mean this
    replaced failed exactly here — it averaged everything, so every row moved.
    """
    df = _frame(n_samples=180)
    engineered, _ = offline_baseline.add_window_features(df.copy(), ["xmeas_1"])

    tampered = df.copy()
    last_sample = tampered["sample"].max()
    tampered.loc[tampered["sample"] == last_sample, "xmeas_1"] += 1000.0
    engineered_after, _ = offline_baseline.add_window_features(tampered, ["xmeas_1"])

    early = engineered["sample"] < last_sample
    pd.testing.assert_series_equal(
        engineered.loc[early, "xmeas_1_deviation"],
        engineered_after.loc[early, "xmeas_1_deviation"],
    )


def test_equipment_histories_do_not_leak_into_each_other():
    """Serving keys the aggregate by equipment, so offline windowing must be scoped the same way:
    one equipment's samples must never form another's baseline."""
    df = _frame(n_samples=120, equipments=("eq-1", "eq-2"))
    engineered, _ = offline_baseline.add_window_features(
        df.copy(), ["xmeas_1"], group_cols=GROUP_COLS,
    )

    for equipment in ("eq-1", "eq-2"):
        rows = engineered[engineered["equipment_id"] == equipment]
        first_window = rows[
            offline_baseline.window_index(rows["timestamp"]) ==
            offline_baseline.window_index(rows["timestamp"]).min()
        ]
        # If eq-2 borrowed eq-1's windows, its first window would have a baseline and be non-zero.
        assert (first_window["xmeas_1_deviation"] == 0.0).all()


def test_unknown_group_column_is_rejected():
    """A mistyped group column would silently window the whole frame as one history — that is the
    cross-equipment leak above, arriving quietly. Fail instead."""
    df = _frame(n_samples=70)
    with pytest.raises(ValueError, match="group column"):
        offline_baseline.add_window_features(df, ["xmeas_1"], group_cols=["simulation_run"])


def test_row_order_is_preserved():
    """The helper sorts internally to compute causally; it must hand rows back in the caller's
    order, or the features would be silently misaligned with the labels."""
    df = _frame(n_samples=70)
    engineered, _ = offline_baseline.add_window_features(df.copy(), ["xmeas_1"])
    pd.testing.assert_series_equal(engineered["sample"], df["sample"])
    pd.testing.assert_series_equal(engineered["xmeas_1"], df["xmeas_1"])
