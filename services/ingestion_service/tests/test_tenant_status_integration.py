# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The tenant-status stream through a real Kafka broker (ADR-0033 dec 3, ADR-0026 dec 11).

test_tenant_status.py (ingestion) and test_tenant_status_publisher.py (alert worker) each fake the
other side and the broker between them. Here both run on the platform's broker image with the
services' own clients: the alert worker's producer (AlertKafkaConsumer.start, whose send_and_wait the
worker hands the publisher, AlertKafkaConsumer.send) and ingestion's follower consumer (main.tenant_status_consumer, what its
startup follows), on a topic of the test's own.

- a suspended tenant is held suspended, an active one is not, and a reactivation published is heard;
- a follower started after the publisher reads from the newest offset: it learns every tenant within
  one publish interval, and a status sent before it started is never applied;
- a message on the stream that is not a status is ignored and the follower keeps following;
- both sides outlive a broker outage longer than a send's timeout: a suspension published after the
  broker returns is heard, by the same publisher and follower;
- the running service refuses a suspended tenant's device and accepts an active tenant's.

The registry read is a stand-in pool that returns rows: the publisher's SELECT itself is proven on a
real database by infrastructure/timescaledb/tests/test_writers_read_tenant_status.py.
"""
import asyncio
import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from aiokafka import AIOKafkaProducer

from industryflow_contracts import tenancy, tenant_status as contract
from real_kafka import BOOTSTRAP, broker_outage, create_topics, records, require_broker, require_outage, unique_topic

# The alert worker's modules, after ingestion's own on the path: ingestion's config and main win.
sys.path.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "alert_service", "worker"))

import config  # noqa: E402
import main  # noqa: E402
import tenant_status  # noqa: E402
import tenant_status_publisher  # noqa: E402
from kafka_consumer import AlertKafkaConsumer  # noqa: E402

pytestmark = pytest.mark.integration

# How often the stand-in registry is published here; the late-follower proof uses the platform's own.
FAST_INTERVAL = 1.0
# A follower's start (metadata, assignment, the newest offset) on top of one interval.
JOIN_SLACK_S = 10
# Longer than a producer's request timeout (aiokafka's default, 40 s): the sends in flight when the
# broker stops expire while it is down.
OUTAGE_S = 50


@pytest.fixture(scope="module", autouse=True)
def broker():
    require_broker()
    here = os.path.dirname(os.path.abspath(__file__))
    assert os.path.dirname(os.path.abspath(config.__file__)) == os.path.dirname(here), config.__file__


class Registry:
    """Stands in for the database pool: ``fetch`` returns the registry rows the test sets."""

    def __init__(self, **tenants):
        self.active = dict(tenants)

    @asynccontextmanager
    async def acquire(self):
        registry = self

        class Conn:
            async def fetch(self, sql, *args):
                return [{"schema_name": tenancy.schema_name(cid), "is_active": active}
                        for cid, active in registry.active.items()]

        yield Conn()


@pytest.fixture
def topic(monkeypatch):
    """A tenant-status topic of the test's own, named where both services read it, and a fresh status."""
    name = unique_topic("if-tenant-status-it")
    asyncio.run(create_topics(name))
    settings = config.get_settings()
    monkeypatch.setattr(settings, "KAFKA_BOOTSTRAP_SERVERS", BOOTSTRAP)
    monkeypatch.setattr(settings, "KAFKA_TOPIC_TENANT_STATUS", name)
    monkeypatch.setattr(settings, "KAFKA_TOPIC_SENSOR_DATA", unique_topic("if-ingest-it"))
    monkeypatch.setattr(tenant_status, "status", tenant_status.TenantStatus())
    return name


def _worker_config(tmp_path):
    sensors = unique_topic("if-alert-unused")
    return SimpleNamespace(
        KAFKA_BOOTSTRAP_SERVERS=BOOTSTRAP, KAFKA_TOPIC_SENSOR_DATA=sensors, KAFKA_GROUP_ID=f"{sensors}-group",
        KAFKA_TOPIC_DLQ=f"{sensors}.dlq", KAFKA_MAX_RETRIES=1, KAFKA_RETRY_BACKOFF_SECONDS=0.05,
        KAFKA_MAX_BACKOFF_SECONDS=0.2, HEARTBEAT_FILE=str(tmp_path / "heartbeat"),
    )


@asynccontextmanager
async def publishing(registry, topic, tmp_path, interval):
    """The worker's publisher loop on the worker's own producer, as AlertService.run starts it."""
    worker = AlertKafkaConsumer(_worker_config(tmp_path), rules_engine=None)
    await worker.start()
    task = asyncio.create_task(tenant_status_publisher.run(registry, worker.send, topic, interval))
    try:
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.stop()


@asynccontextmanager
async def following():
    """Ingestion's follower on the consumer its startup builds."""
    task = asyncio.create_task(tenant_status.follow(main.tenant_status_consumer, retry_seconds=1.0))
    try:
        yield task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def _until(check, what, timeout=60, task=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if task is not None and task.done():
            raise AssertionError(f"the follower ended: {task.exception() if not task.cancelled() else 'cancelled'}")
        if check():
            return time.monotonic()
        await asyncio.sleep(0.1)
    raise AssertionError(f"not within {timeout}s: {what}")


def _heard(cid):
    return cid in tenant_status.status._heard


def test_suspension_and_reactivation_reach_the_follower(topic, tmp_path):
    active, suspended = str(uuid.uuid4()), str(uuid.uuid4())
    registry = Registry(**{active: True, suspended: False})

    async def scenario():
        async with following() as follower, publishing(registry, topic, tmp_path, FAST_INTERVAL):
            await _until(lambda: _heard(active) and _heard(suspended), "both tenants heard", task=follower)
            assert tenant_status.status.is_suspended(suspended)
            assert not tenant_status.status.is_suspended(active)

            registry.active[suspended] = True
            await _until(lambda: not tenant_status.status.is_suspended(suspended), "the reactivation heard",
                         task=follower)
            registry.active[active] = False
            await _until(lambda: tenant_status.status.is_suspended(active), "the suspension heard", task=follower)
            assert not tenant_status.status.is_suspended(suspended)

        # What the follower heard is what the broker holds: one message per tenant, keyed by it.
        sent = [(r.key.decode(), contract.decode(r.value)) for r in await records(topic)]
        assert sent and {key for key, _ in sent} == {active, suspended}
        assert all(key == cid for key, (cid, _) in sent)

    asyncio.run(scenario())


def test_a_follower_started_after_the_publisher_learns_every_tenant_within_one_interval(topic, tmp_path):
    tenants = {str(uuid.uuid4()): active for active in (True, True, False)}
    registry = Registry(**tenants)
    # A status from before the follower started, for a tenant the registry no longer holds: read from
    # the newest offset, the follower never applies it.
    gone = str(uuid.uuid4())
    interval = contract.PUBLISH_INTERVAL_SECONDS

    async def scenario():
        producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP, acks="all")
        await producer.start()
        try:
            key, value = contract.encode(gone, False)
            await producer.send_and_wait(topic, value=value, key=key)
        finally:
            await producer.stop()
        async with publishing(registry, topic, tmp_path, interval):
            await asyncio.sleep(1)  # the first round is on the topic before the follower starts
            started = time.monotonic()
            async with following() as follower:
                learned = await _until(lambda: all(_heard(cid) for cid in tenants), "every tenant heard",
                                       timeout=interval + JOIN_SLACK_S, task=follower)
                assert learned - started <= interval + JOIN_SLACK_S
                assert {cid: not tenant_status.status.is_suspended(cid) for cid in tenants} == tenants
                assert not _heard(gone) and not tenant_status.status.is_suspended(gone)

    asyncio.run(scenario())


def test_a_message_that_is_not_a_status_is_ignored_and_the_stream_still_followed(topic, tmp_path):
    cid = str(uuid.uuid4())
    registry = Registry(**{cid: True})
    noise = [b"\xffnot json", b"[]", json.dumps({"company_id": cid, "active": "no"}).encode(),
             json.dumps({"company_id": "not-a-uuid", "active": False}).encode()]

    async def scenario():
        async with following() as follower:
            producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP, acks="all")
            await producer.start()
            try:
                # The follower is at the newest offset before anything is sent.
                await asyncio.sleep(3)
                for value in noise:
                    await producer.send_and_wait(topic, value=value, key=cid.encode())
            finally:
                await producer.stop()
            async with publishing(registry, topic, tmp_path, FAST_INTERVAL):
                await _until(lambda: _heard(cid), "the status after the noise heard", task=follower)
                assert not tenant_status.status.is_suspended(cid)
                registry.active[cid] = False
                await _until(lambda: tenant_status.status.is_suspended(cid), "a later suspension heard",
                             task=follower)
        assert len(await records(topic)) > len(noise)

    asyncio.run(scenario())


def test_publisher_and_follower_outlive_a_broker_outage(topic, tmp_path):
    """The worker's idempotent producer lost its sequence in a broker restart (a numbered batch expired
    unsent) and failed every later status with OutOfOrderSequenceNumber until the worker restarted, so
    ingestion forgot every suspension once the last status went stale."""
    require_outage()
    cid = str(uuid.uuid4())
    registry = Registry(**{cid: True})

    async def scenario():
        async with following() as follower, publishing(registry, topic, tmp_path, FAST_INTERVAL):
            await _until(lambda: _heard(cid), "the tenant heard before the outage", task=follower)
            async with broker_outage():
                await asyncio.sleep(OUTAGE_S)
            registry.active[cid] = False
            await _until(lambda: tenant_status.status.is_suspended(cid), "a suspension after the outage heard",
                         timeout=120, task=follower)

    asyncio.run(scenario())


def _headers(pem):
    return {"X-Client-Verify": "SUCCESS", "X-Client-Cert": quote(pem)}


def test_the_running_service_refuses_a_suspended_tenants_device(topic, tmp_path, make_cert, sample_body):
    from fastapi.testclient import TestClient

    active, suspended = str(uuid.uuid4()), str(uuid.uuid4())
    registry = Registry(**{active: True, suspended: False})
    certs = {cid: make_cert(f"industryflow:tenant/{cid}/device/press-1") for cid in (active, suspended)}

    async def publish_until(stop):
        async with publishing(registry, topic, tmp_path, FAST_INTERVAL):
            await stop.wait()

    # The service's own startup: its producer and its follower, on this test's topics.
    with TestClient(main.app) as c:
        stop = c.portal.call(asyncio.Event)
        publisher = c.portal.start_task_soon(publish_until, stop)
        try:
            deadline = time.monotonic() + 60
            while not tenant_status.status.is_suspended(suspended):
                assert time.monotonic() < deadline, "the service never heard the suspension"
                assert not publisher.done(), publisher.exception()
                time.sleep(0.2)
            r = c.post("/ingest", json=sample_body, headers=_headers(certs[suspended]))
            assert r.status_code == 403 and r.json()["detail"] == "Tenant suspended", r.text
            r = c.post("/ingest", json=sample_body, headers=_headers(certs[active]))
            assert r.status_code == 202, r.text
        finally:
            c.portal.call(stop.set)
            publisher.result(timeout=30)
        readings = [json.loads(r.value) for r in asyncio.run(records(config.get_settings().KAFKA_TOPIC_SENSOR_DATA))]
    assert [m["company_id"] for m in readings] == [active]
