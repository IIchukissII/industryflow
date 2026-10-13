# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The aggregation-window contract (ADR-0006 dec 7)."""
import pytest

from industryflow_contracts import windows


def test_the_contract_values():
    """Pinned on purpose. Every served feature and every trained model is computed over these
    windows (ADR-0023 dec 9), so a change here must be a reviewed change to this test too — never a
    number that drifted on its own."""
    assert dict(windows.WINDOWS) == {
        "1min": windows.Window("sensor_aggregations_1min", 60, 120),
        "5min": windows.Window("sensor_aggregations_5min", 300, 600),
        "1hour": windows.Window("sensor_aggregations_1hour", 3600, 900),
    }
    assert windows.DEFAULT_GRANULARITY in windows.WINDOWS


def test_the_contract_cannot_be_changed_at_runtime():
    with pytest.raises(TypeError):
        windows.WINDOWS["1min"] = windows.Window("x", 1, 1)
    with pytest.raises(AttributeError):
        windows.WINDOWS["1min"].width_seconds = 1


@pytest.mark.parametrize("granularity,t,expected", [
    # 10:05:30 with 1-minute windows and 2 minutes of lateness: the newest written row is 10:02's.
    ("1min", 36330, 36120 // 60),
    ("5min", 36330, (36330 - 600) // 300 - 1),
    ("1hour", 36330, (36330 - 900) // 3600 - 1),
])
def test_latest_emitted_window(granularity, t, expected):
    assert windows.latest_emitted_window(t, granularity) == expected


def test_an_unknown_granularity_is_refused_by_name():
    with pytest.raises(ValueError, match="1min"):
        windows.window("2min")
