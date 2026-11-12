# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Unit tests for the alert-label precision math (ADR-0022 dec 3).

The SQL aggregation runs against a real DB in the db-tenant-isolation CI (see
infrastructure/timescaledb/tests/test_alert_precision.py); here we pin the pure precision
helper the endpoint folds those counts through — no DB needed. conftest.py
sets the import path + settings so the router module imports in-process.
"""
from routers.alerts_history import _precision, _summary


def test_precision_basic():
    # 3 true positives, 1 false positive → 0.75
    assert _precision(3, 1) == 0.75


def test_the_summary_carries_no_false_positive_rate():
    # Over decided labels it is one minus precision: a second number for one fact (ADR-0022 dec 3).
    assert "false_positive_rate" not in _summary(3, 1, 0).model_dump()


def test_none_when_nothing_decisive():
    # No true/false positives labelled → precision is undefined, not a fake zero.
    assert _precision(0, 0) is None


def test_unsure_is_excluded_from_the_denominator():
    # 'unsure' verdicts never reach these helpers — the endpoint counts them separately and
    # passes only tp/fp. So an even tp/fp split is 0.5 regardless of how many were unsure.
    assert _precision(2, 2) == 0.5


def test_perfect_and_zero_precision():
    assert _precision(5, 0) == 1.0
    assert _precision(0, 5) == 0.0
