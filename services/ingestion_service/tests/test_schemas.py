# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reading schema rejects a timestamp beyond the clock-skew bound at the edge."""
import uuid
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from pydantic import ValidationError

from schemas import MAX_FUTURE_SKEW_SECONDS, SensorDataInput


def _body(timestamp):
    return {"timestamp": timestamp, "sensor_id": str(uuid.uuid4()), "equipment_id": str(uuid.uuid4()),
            "site_id": "site-1", "value": 1.0}


def test_past_and_present_timestamps_are_accepted():
    now = datetime.now(timezone.utc)
    for ts in (now, now - timedelta(days=400), now + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS - 30)):
        SensorDataInput(**_body(ts.isoformat()))


@pytest.mark.parametrize("ahead", [timedelta(seconds=MAX_FUTURE_SKEW_SECONDS + 60), timedelta(days=365)])
def test_a_timestamp_beyond_the_skew_bound_is_rejected(ahead):
    with pytest.raises(ValidationError, match="ahead of the server clock"):
        SensorDataInput(**_body((datetime.now(timezone.utc) + ahead).isoformat()))


def test_a_naive_timestamp_is_read_as_utc():
    future = (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None)
    with pytest.raises(ValidationError):
        SensorDataInput(**_body(future.isoformat()))


def test_the_endpoint_refuses_a_future_reading_and_produces_nothing(client, make_cert, sample_body):
    c, produced = client
    pem = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-01")
    body = dict(sample_body, timestamp=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat())
    r = c.post("/ingest", json=body, headers={"x-client-verify": "SUCCESS", "x-client-cert": quote(pem)})
    assert r.status_code == 422
    assert produced == []
