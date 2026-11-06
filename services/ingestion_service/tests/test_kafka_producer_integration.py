# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Ingestion's producer against a real Kafka broker (ADR-0005 dec 5, ADR-0026 dec 11).

The other ingestion tests replace send_sensor_data: they prove the endpoint's mapping of a failed send
to 503, not that a send fails when the broker is gone, nor that one counted as sent is on the topic.
Here the service runs its own startup (AsyncKafkaProducerSingleton, acks=all) on the platform's
broker, with the readings on a topic of the test's own, and each outcome is read back from the broker:

- a reading answered 202 is on the topic, keyed by its sensor;
- with the broker stopped, the endpoint answers 503 and send_sensor_data reports failure, never
  success, and neither reading reaches the topic later;
- once the broker is back, the same running service delivers the next reading, exactly once.

The outage is real: the compose broker is stopped and started again (real_kafka.broker_stopped).
"""
import asyncio
import json
import time
import uuid
from urllib.parse import quote

import pytest

from real_kafka import (  # noqa: F401 - topics_deleted is an autouse fixture
    BOOTSTRAP, broker_stopped, records, require_broker, require_outage, topics_deleted, unique_topic,
)

import config
import kafka_producer
import main

pytestmark = pytest.mark.integration

# A send to a stopped broker fails within this.
SEND_DEADLINE_S = 120


@pytest.fixture(scope="module", autouse=True)
def broker():
    require_broker()
    require_outage()


@pytest.fixture
def service(monkeypatch):
    """The service's own startup on topics of the test's own: ``(client, readings topic)``."""
    from fastapi.testclient import TestClient

    settings = config.get_settings()
    topic = unique_topic("if-ingest-it")
    monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", BOOTSTRAP)
    monkeypatch.setattr(settings, "KAFKA_TOPIC_SENSOR_DATA", topic)
    monkeypatch.setattr(settings, "KAFKA_TOPIC_TENANT_STATUS", unique_topic("if-tenant-status-it"))
    with TestClient(main.app) as c:
        yield c, topic


def _headers(pem):
    return {"X-Client-Verify": "SUCCESS", "X-Client-Cert": quote(pem)}


def _body(value):
    return {"timestamp": "2026-11-06T10:30:00Z", "sensor_id": str(uuid.uuid4()), "equipment_id": str(uuid.uuid4()),
            "site_id": "it", "value": value, "unit": "celsius", "quality_code": 0}


def _on_topic(topic):
    return [(r.key.decode(), json.loads(r.value)) for r in asyncio.run(records(topic))]


def test_a_reading_is_delivered_refused_while_the_broker_is_down_and_delivered_once_after(service, make_cert):
    c, topic = service
    company_id = str(uuid.uuid4())
    headers = _headers(make_cert(f"industryflow:tenant/{company_id}/device/press-1"))

    before = _body(1.0)
    r = c.post("/ingest", json=before, headers=headers)
    assert r.status_code == 202, r.text
    on_topic = _on_topic(topic)
    assert [(key, m["value"], m["company_id"]) for key, m in on_topic] == [(before["sensor_id"], 1.0, company_id)]

    during, direct = _body(2.0), _body(3.0)
    with broker_stopped():
        started = time.monotonic()
        r = c.post("/ingest", json=during, headers=headers)
        assert r.status_code == 503, f"{r.status_code} with the broker stopped: {r.text}"
        assert time.monotonic() - started < SEND_DEADLINE_S
        message = {**direct, "company_id": company_id}
        assert c.portal.call(kafka_producer.send_sensor_data, message) is False

    after = _body(4.0)
    r = c.post("/ingest", json=after, headers=headers)
    assert r.status_code == 202, f"{r.status_code} once the broker is back: {r.text}"
    on_topic = _on_topic(topic)
    # The refused readings never arrive; the one sent after the outage arrives once.
    assert [(key, m["value"]) for key, m in on_topic] == [(before["sensor_id"], 1.0), (after["sensor_id"], 4.0)]
