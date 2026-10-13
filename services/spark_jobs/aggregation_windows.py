# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The aggregation windows the Spark job materializes, and when each window's row appears.

This is the producer's half of the train/serve binding (ADR-0023 rev 2 dec 9). The serving baseline
reads the most recent row of ``sensor_aggregations_<granularity>``; with append-mode emission
(ADR-0006 dec 2) a window's row is written only once the watermark — the latest event time seen,
minus the allowed lateness — has passed the window's end. So at time ``t`` the newest row is not the
window just before ``t``'s own, but

    closed(t) = floor((t - watermark) / width) - 1

and training must describe a row by that same window. Both numbers that decide it, the width and the
allowed lateness, are fixed here and nowhere else in this job; the offline re-derivation
(services/ml_service/notebooks/utils/offline_baseline.py) and the serving provider carry the same
table, and a test asserts the three agree. Changing a value is a feature-contract change, made here
where review sees it — not an environment override.

Kept dependency-free so it is importable without pyspark.
"""
from __future__ import annotations

# granularity -> (table, window width in seconds, allowed lateness (watermark delay) in seconds)
AGGREGATION_WINDOWS = {
    "1min": ("sensor_aggregations_1min", 60, 120),
    "5min": ("sensor_aggregations_5min", 300, 600),
    "1hour": ("sensor_aggregations_1hour", 3600, 900),
}


def window_duration(granularity: str) -> str:
    """The Spark ``window()`` duration of a granularity."""
    return f"{AGGREGATION_WINDOWS[granularity][1]} seconds"


def watermark_delay(granularity: str) -> str:
    """The Spark ``withWatermark()`` delay of a granularity."""
    return f"{AGGREGATION_WINDOWS[granularity][2]} seconds"


def table_name(granularity: str) -> str:
    return AGGREGATION_WINDOWS[granularity][0]


def latest_emitted_window(epoch_seconds: int, granularity: str) -> int:
    """Index of the newest window whose row has been written by event time ``epoch_seconds``.

    A window ``m`` covers ``[m * width, (m + 1) * width)`` and is emitted once the watermark
    (``epoch_seconds - lateness``) reaches its end.
    """
    _, width, lateness = AGGREGATION_WINDOWS[granularity]
    return (epoch_seconds - lateness) // width - 1
