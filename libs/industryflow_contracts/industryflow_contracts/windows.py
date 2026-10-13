# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The aggregation windows: one width and one allowed lateness per granularity (ADR-0006 dec 7).

The Spark aggregation job writes ``sensor_aggregations_<granularity>``; the serving provider reads its
newest closed window as a model feature; training re-derives that same window offline (ADR-0023
dec 9). With append-mode emission (ADR-0006 dec 2) a window's row appears only once the watermark,
the latest event time minus the allowed lateness, passes the window's end, so at time ``t`` the
newest row is

    closed(t) = floor((t - lateness) / width) - 1

and the three components agree on what a feature means only if they use the same two numbers. They
are therefore part of the code that defines the granularity, not deployment settings, and they are
defined here once for all three. Changing a value changes what every served feature means: it is a
reviewed change to this contract, never a configuration edit.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True)
class Window:
    """One aggregation granularity: the table it is written to, its width and its allowed lateness."""

    table: str
    width_seconds: int
    lateness_seconds: int


# granularity -> window. Read-only, so no importer can change the contract at runtime.
WINDOWS: Mapping[str, Window] = MappingProxyType({
    "1min": Window("sensor_aggregations_1min", 60, 120),
    "5min": Window("sensor_aggregations_5min", 300, 600),
    "1hour": Window("sensor_aggregations_1hour", 3600, 900),
})

# The granularity a feature reads when its config names none.
DEFAULT_GRANULARITY = "1min"


def window(granularity: str) -> Window:
    """The window of a granularity, or ``ValueError`` naming the ones that exist."""
    try:
        return WINDOWS[granularity]
    except KeyError:
        raise ValueError(
            f"unknown granularity {granularity!r}; expected one of {sorted(WINDOWS)}"
        ) from None


def latest_emitted_window(epoch_seconds, granularity: str):
    """Index of the newest window whose row has been written by event time ``epoch_seconds``.

    A window ``m`` covers ``[m * width, (m + 1) * width)`` and is emitted once the watermark
    (``epoch_seconds - lateness``) reaches its end. Pure arithmetic, so ``epoch_seconds`` may be an
    int or anything that supports ``-`` and ``//`` element-wise (a pandas Series).
    """
    w = window(granularity)
    return (epoch_seconds - w.lateness_seconds) // w.width_seconds - 1
