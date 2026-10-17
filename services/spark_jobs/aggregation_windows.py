# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The aggregation windows the Spark job materializes, in the form Spark's API takes them.

This is the producer's half of the train/serve binding (ADR-0023 dec 9). The serving baseline
reads the most recent row of ``sensor_aggregations_<granularity>``; with append-mode emission
(ADR-0006 dec 2) a window's row is written only once the watermark — the latest event time seen,
minus the allowed lateness — has passed the window's end. So at time ``t`` the newest row is not the
window just before ``t``'s own, but

    closed(t) = floor((t - watermark) / width) - 1

and training must describe a row by that same window. Both numbers that decide it, the width and the
allowed lateness, are the window contract (``industryflow_contracts.windows``, ADR-0006 dec 7), which
the serving provider and the offline re-derivation read too. This module only adapts it to the
strings ``window()`` and ``withWatermark()`` take; it holds no numbers of its own. Changing a value is
a feature-contract change, made in the contract where review sees it — not an environment override.

Kept free of pyspark so it is importable without it.
"""
from __future__ import annotations

from industryflow_contracts import windows

# granularity -> (table, window width in seconds, allowed lateness (watermark delay) in seconds),
# a view of the contract in the shape this job iterates.
AGGREGATION_WINDOWS = {
    granularity: (w.table, w.width_seconds, w.lateness_seconds)
    for granularity, w in windows.WINDOWS.items()
}

latest_emitted_window = windows.latest_emitted_window


def window_duration(granularity: str) -> str:
    """The Spark ``window()`` duration of a granularity."""
    return f"{windows.window(granularity).width_seconds} seconds"


def watermark_delay(granularity: str) -> str:
    """The Spark ``withWatermark()`` delay of a granularity."""
    return f"{windows.window(granularity).lateness_seconds} seconds"


def table_name(granularity: str) -> str:
    return windows.window(granularity).table
