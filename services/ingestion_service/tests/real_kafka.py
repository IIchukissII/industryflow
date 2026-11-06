# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What the ingestion service's real-broker proofs share (ADR-0026 dec 11): where the broker is,
whether it answers, a topic of each test's own, reading a topic back as the broker holds it, and a
real outage of the compose broker (`docker compose stop kafka` in the repository root, under the
project its .env names, started again whatever the outcome).

The proofs need a broker at IF_TEST_KAFKA_BOOTSTRAP (default localhost:9092). They skip when none
answers; IF_REQUIRE_REAL_KAFKA=1, set by the CI job that starts the broker, turns the skip into a
failure.
"""
import asyncio
import contextlib
import os
import subprocess
import time
import uuid

import pytest
from aiokafka import AIOKafkaConsumer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

BOOTSTRAP_ENV = "IF_TEST_KAFKA_BOOTSTRAP"
REQUIRE_ENV = "IF_REQUIRE_REAL_KAFKA"
BOOTSTRAP = os.environ.get(BOOTSTRAP_ENV) or "localhost:9092"
DEADLINE_S = 90
ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".."))
# A broker that was stopped answers again within this.
RESTART_DEADLINE_S = 180


async def broker_answers(timeout: float = 15) -> bool:
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP, request_timeout_ms=5000)
    try:
        await asyncio.wait_for(admin.start(), timeout)
        await admin.list_topics()
        return True
    except Exception:
        return False
    finally:
        await admin.close()


def require_broker():
    if not asyncio.run(broker_answers()):
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"{REQUIRE_ENV}=1 but no Kafka broker answers at {BOOTSTRAP}")
        pytest.skip(f"no Kafka broker at {BOOTSTRAP}")


def unique_topic(prefix: str) -> str:
    """A topic no deployed service reads, so a proof run against a live broker disturbs nothing."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


async def create_topics(*topics):
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(t, num_partitions=1, replication_factor=1) for t in topics])
    finally:
        await admin.close()


async def records(topic):
    """Every record on the topic's one partition, read without a group."""
    tp = TopicPartition(topic, 0)
    # From the earliest offset by reset policy: aiokafka 0.15 drops a seek_to_beginning made after
    # end_offsets, and the read then starts at the end and sees nothing.
    consumer = AIOKafkaConsumer(bootstrap_servers=BOOTSTRAP, enable_auto_commit=False,
                                auto_offset_reset="earliest")
    await consumer.start()
    try:
        consumer.assign([tp])
        end = (await consumer.end_offsets([tp]))[tp]
        out = []
        deadline = time.monotonic() + DEADLINE_S
        while len(out) < end and time.monotonic() < deadline:
            batch = await consumer.getmany(tp, timeout_ms=500)
            out.extend(batch.get(tp, []))
        assert len(out) == end, f"read {len(out)} of {end} records from {topic}"
        return out
    finally:
        await consumer.stop()


def _compose(*args, timeout=180):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)


def require_outage():
    """Skip, or fail under REQUIRE_ENV, unless this host can stop and start the compose broker."""
    try:
        ps = _compose("ps", "--status", "running", "--services", timeout=60)
    except FileNotFoundError:
        ps = None
    if ps is None or ps.returncode != 0 or "kafka" not in ps.stdout.split():
        why = "docker compose cannot reach the broker's container" if ps is None else (ps.stderr or ps.stdout)
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"{REQUIRE_ENV}=1 but the outage cannot be staged: {why}")
        pytest.skip(f"the outage cannot be staged: {why}")


def _stop():
    stopped = _compose("stop", "kafka")
    assert stopped.returncode == 0, stopped.stderr


def _start():
    started = _compose("start", "kafka")
    assert started.returncode == 0, started.stderr
    deadline = time.monotonic() + RESTART_DEADLINE_S
    while not asyncio.run(broker_answers(timeout=5)):
        assert time.monotonic() < deadline, f"the broker did not answer within {RESTART_DEADLINE_S}s of its start"
        time.sleep(2)


@contextlib.contextmanager
def broker_stopped():
    """The compose broker stopped for the body; started again, and answering, afterwards."""
    _stop()
    try:
        yield
    finally:
        _start()


@contextlib.asynccontextmanager
async def broker_outage():
    """broker_stopped, for a body that runs on an event loop."""
    await asyncio.to_thread(_stop)
    try:
        yield
    finally:
        await asyncio.to_thread(_start)
