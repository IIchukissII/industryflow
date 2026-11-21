# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reading schema rejects a timestamp beyond the clock-skew bound at the edge."""
import json
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


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e999", '"NaN"', '"inf"', "1e308", "-1.5e308", "1.1e150"])
def test_the_endpoint_refuses_a_value_that_is_not_a_finite_number(client, make_cert, sample_body, value):
    # ADR-0044 dec 1. The body is sent as written: Python's JSON reads NaN, Infinity and 1e999.
    c, produced = client
    pem = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-01")
    body = json.dumps(dict(sample_body, value=0.0)).replace('"value": 0.0', f'"value": {value}')
    r = c.post("/ingest", content=body, headers={"content-type": "application/json", "x-client-verify": "SUCCESS",
                                                 "x-client-cert": quote(pem)})
    assert r.status_code == 422, r.text
    assert produced == []


@pytest.mark.parametrize("value", ["[NaN]", '{"x": NaN}', "NaN"])
def test_a_non_finite_number_inside_a_refused_body_is_a_422(client, make_cert, sample_body, value):
    # The 422 echoes the refused input, here holding NaN: as a whole body missing a field, or nested.
    c, produced = client
    pem = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-01")
    body = {k: v for k, v in sample_body.items() if k != "sensor_id"} if value == "NaN" else sample_body
    text = json.dumps(dict(body, value=0.0)).replace('"value": 0.0', f'"value": {value}')
    r = c.post("/ingest", content=text, headers={"content-type": "application/json", "x-client-verify": "SUCCESS",
                                                 "x-client-cert": quote(pem)})
    assert r.status_code == 422, r.text
    assert produced == []


def test_the_readings_contract_is_what_ingestion_accepts():
    # A device builds its body from industryflow_contracts.readings; ingestion validates it here.
    from industryflow_contracts import readings

    assert set(SensorDataInput.model_fields) == set(readings.REQUIRED_FIELDS + readings.OPTIONAL_FIELDS)
    assert {n for n, f in SensorDataInput.model_fields.items() if f.is_required()} == set(readings.REQUIRED_FIELDS)
    body = readings.reading(timestamp=datetime.now(timezone.utc).isoformat(), sensor_id=str(uuid.uuid4()),
                            equipment_id=str(uuid.uuid4()), site_id="site", value=1.5, unit="bar", quality_code=0)
    assert SensorDataInput.model_validate(body).value == 1.5
