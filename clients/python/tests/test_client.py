# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ergonomics tests for the notebook client — URL/params/auth and DataFrame shaping, no server."""
from datetime import datetime

import pytest

pytest.importorskip("pandas")

from industryflow import IndustryFlowClient  # noqa: E402


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeSession:
    """Records the last GET and returns a queued payload; mimics requests.Session."""

    def __init__(self, payload):
        self.headers: dict = {}
        self._payload = payload
        self.last = None

    def get(self, url, params=None, verify=True, timeout=None):
        self.last = {"url": url, "params": params, "verify": verify}
        return _FakeResponse(self._payload)


def test_capability_header_is_set():
    sess = _FakeSession([])
    IndustryFlowClient("https://api.local", token="cap-123", session=sess)
    assert sess.headers["X-IF-Capability"] == "cap-123"
    assert "Authorization" not in sess.headers


def test_measurements_builds_request_and_frame():
    payload = [
        {"time": "2026-06-27T10:00:00Z", "sensor_id": "s1", "value": 1.0},
        {"time": "2026-06-27T10:01:00Z", "sensor_id": "s1", "value": 2.0},
    ]
    sess = _FakeSession(payload)
    client = IndustryFlowClient("https://api.local/", session=sess)

    df = client.measurements(
        sensor_id="s1",
        start=datetime(2026, 6, 27, 10, 0, 0),
        end=datetime(2026, 6, 27, 11, 0, 0),
        order="asc",
        limit=500,
    )

    # base_url trailing slash trimmed; path appended.
    assert sess.last["url"] == "https://api.local/api/measurements"
    # datetimes serialized to ISO; None filters dropped; equipment_id absent.
    assert sess.last["params"]["start"] == "2026-06-27T10:00:00"
    assert sess.last["params"]["order"] == "asc"
    assert "equipment_id" not in sess.last["params"]
    # rows shaped into a DataFrame with a parsed time column.
    assert list(df["value"]) == [1.0, 2.0]
    assert str(df["time"].dtype).startswith("datetime64")


def test_training_data_unwraps_data_key():
    payload = {"equipment_id": "e1", "data": [{"time": "2026-06-27T10:00:00Z", "value": 3.0}]}
    sess = _FakeSession(payload)
    client = IndustryFlowClient("https://api.local", session=sess)

    df = client.training_data("e1", lookback_days=7)

    assert sess.last["url"] == "https://api.local/api/training-data/equipment/e1"
    assert sess.last["params"]["lookback_days"] == 7
    assert list(df["value"]) == [3.0]
