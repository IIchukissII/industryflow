# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A training frame's times are instants: naive UTC, `Z`, an offset or an aware column give one feature."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'notebooks', 'utils'))

import offline_baseline  # noqa: E402

NAIVE_UTC = ["2026-11-20 10:00:10", "2026-11-20 10:00:50", "2026-11-20 10:01:20", "2026-11-20 10:04:30"]
VALUES = [1.0, 3.0, 10.0, 7.0]


def _features(timestamps):
    df = pd.DataFrame({"timestamp": timestamps, "pressure": VALUES})
    out, _ = offline_baseline.add_window_features(df, ["pressure"], granularity="1min",
                                                  stats=("window_mean",))
    return out["pressure_window_mean"].tolist()


EXPECTED = _features(NAIVE_UTC)


@pytest.mark.parametrize("timestamps", [
    [t.replace(" ", "T") + "Z" for t in NAIVE_UTC],
    # One hour east: the same instants, written as local times with their offset.
    ["2026-11-20T11:00:10+01:00", "2026-11-20T11:00:50+01:00", "2026-11-20T11:01:20+01:00",
     "2026-11-20T11:04:30+01:00"],
    pd.to_datetime(pd.Series(NAIVE_UTC)).dt.tz_localize("UTC").dt.tz_convert("Europe/Berlin"),
    # Mixed offsets, whose text order is not their time order.
    ["2026-11-20T10:00:10Z", "2026-11-20T09:00:50-01:00", "2026-11-20T11:01:20+01:00", "2026-11-20T10:04:30Z"],
])
def test_aware_times_give_the_feature_of_their_utc_instants(timestamps):
    assert _features(timestamps) == EXPECTED


def test_the_reference_frame_has_a_written_window():
    # At 10:04:30 the newest written window is 10:01 (two minutes' lateness), holding the 10:01:20
    # reading: the comparison above is not all neutral zeros.
    assert EXPECTED[-1] == 10.0
