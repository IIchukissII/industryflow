# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert worker's consumer loop against a real Kafka broker (ADR-0005 dec 2-4, ADR-0026 dec 11).

test_kafka_consumer.py fakes the client at getmany / commit / committed / seek: it proves what the
loop asks of the broker, not what the broker does with it. Here the worker's own AIOKafkaConsumer
runs against the platform's broker image, with only the rules engine replaced, and each outcome is
read back from the broker: the group's committed offset and the dead-letter topic's records.

- a new group starts at the earliest offset and commits past the last handled reading (dec 2, 4);
- a worker killed with a batch in flight loses nothing: the next member of the group is handed the
  uncommitted readings and none that were committed (dec 2);
- an infrastructure failure rewinds to the last commit and the reading is processed again, never
  dead-lettered (dec 3);
- a malformed or invalid reading, and one whose retries run out, lands on the real dead-letter
  topic with its envelope, and the offset moves past it (dec 3).

Needs a broker at IF_TEST_KAFKA_BOOTSTRAP (default localhost:9092). It skips when none answers;
IF_REQUIRE_REAL_KAFKA=1, set by the CI job that starts the broker, turns that skip into a failure.
"""
import asyncio
import collections
import json
import os
import signal
import subprocess
import sys
import time
import uuid
from types import SimpleNamespace

import pytest
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.admin import AIOKafkaAdminClient, NewTopic

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "worker"))

from kafka_consumer import DEAD_LETTER_CONSUMER, AlertKafkaConsumer  # noqa: E402

pytestmark = pytest.mark.integration

BOOTSTRAP_ENV = "IF_TEST_KAFKA_BOOTSTRAP"
REQUIRE_ENV = "IF_REQUIRE_REAL_KAFKA"
BOOTSTRAP = os.environ.get(BOOTSTRAP_ENV) or "localhost:9092"
CO = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
SENSOR = "11111111-1111-1111-1111-111111111111"
# Joining a group waits out the broker's initial rebalance delay, and after a kill, the dead
# member's session; every wait below is bounded by this.
DEADLINE_S = 90


def _reading(seq, **over):
    reading = {"company_id": CO, "sensor_id": SENSOR, "value": 1.0, "seq": seq}
    reading.update(over)
    return json.dumps(reading).encode()


async def _broker_answers():
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP, request_timeout_ms=5000)
    try:
        await asyncio.wait_for(admin.start(), 15)
        await admin.list_topics()
        return True
    except Exception:
        return False
    finally:
        await admin.close()


@pytest.fixture(scope="module", autouse=True)
def broker():
    if not asyncio.run(_broker_answers()):
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"{REQUIRE_ENV}=1 but no Kafka broker answers at {BOOTSTRAP}")
        pytest.skip(f"no Kafka broker at {BOOTSTRAP}")


@pytest.fixture
def names():
    """A topic, its dead-letter topic and a group of their own: tests share no state on the broker."""
    topic = f"if-alert-it-{uuid.uuid4().hex[:12]}"
    asyncio.run(_create_topics(topic, f"{topic}.dlq"))
    return SimpleNamespace(topic=topic, dlq=f"{topic}.dlq", group=f"{topic}-group")


async def _create_topics(*topics):
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP)
    await admin.start()
    try:
        await admin.create_topics([NewTopic(t, num_partitions=1, replication_factor=1) for t in topics])
    finally:
        await admin.close()


async def _produce(topic, *values):
    producer = AIOKafkaProducer(bootstrap_servers=BOOTSTRAP, acks="all", enable_idempotence=True)
    await producer.start()
    try:
        for value in values:
            await producer.send_and_wait(topic, value)
    finally:
        await producer.stop()


async def _committed(group, topic):
    """The group's committed offset on the topic's one partition, as the broker holds it."""
    admin = AIOKafkaAdminClient(bootstrap_servers=BOOTSTRAP)
    await admin.start()
    try:
        offsets = await admin.list_consumer_group_offsets(group)
    finally:
        await admin.close()
    meta = offsets.get(TopicPartition(topic, 0))
    return None if meta is None or meta.offset < 0 else meta.offset


async def _wait_committed(group, topic, offset):
    deadline = time.monotonic() + DEADLINE_S
    while time.monotonic() < deadline:
        if await _committed(group, topic) == offset:
            return
        await asyncio.sleep(0.25)
    raise AssertionError(f"{group} never committed {topic} at {offset} (at {await _committed(group, topic)})")


async def _records(topic):
    """Every record on the topic's one partition, read without a group."""
    tp = TopicPartition(topic, 0)
    consumer = AIOKafkaConsumer(bootstrap_servers=BOOTSTRAP, enable_auto_commit=False)
    await consumer.start()
    try:
        consumer.assign([tp])
        end = (await consumer.end_offsets([tp]))[tp]
        await consumer.seek_to_beginning(tp)
        out = []
        deadline = time.monotonic() + DEADLINE_S
        while len(out) < end and time.monotonic() < deadline:
            batch = await consumer.getmany(tp, timeout_ms=500)
            out.extend(batch.get(tp, []))
        assert len(out) == end, f"read {len(out)} of {end} records from {topic}"
        return out
    finally:
        await consumer.stop()


def _config(names, tmp_path, **over):
    config = dict(
        KAFKA_BOOTSTRAP_SERVERS=BOOTSTRAP, KAFKA_TOPIC_SENSOR_DATA=names.topic, KAFKA_GROUP_ID=names.group,
        KAFKA_TOPIC_DLQ=names.dlq, KAFKA_MAX_RETRIES=2, KAFKA_RETRY_BACKOFF_SECONDS=0.05,
        KAFKA_MAX_BACKOFF_SECONDS=0.2, HEARTBEAT_FILE=str(tmp_path / "heartbeat"),
    )
    config.update(over)
    return SimpleNamespace(**config)


class Engine:
    """Stands in for the rules engine: records each reading's seq, and fails as a test scripts it."""

    def __init__(self, fail=None):
        self.seen = []
        self._fail = fail or (lambda seq, count: None)

    async def evaluate(self, reading, company_id):
        seq = reading["seq"]
        self.seen.append(seq)
        error = self._fail(seq, self.seen.count(seq))
        if error is not None:
            raise error
        return []


async def _run_until_committed(config, engine, offset):
    """Run the worker's loop until the broker holds the group's commit at `offset`, then stop it."""
    consumer = AlertKafkaConsumer(config, engine)
    await consumer.start()
    loop = asyncio.create_task(consumer.consume_messages())
    try:
        waiter = asyncio.create_task(_wait_committed(config.KAFKA_GROUP_ID, config.KAFKA_TOPIC_SENSOR_DATA, offset))
        done, _ = await asyncio.wait({loop, waiter}, return_when=asyncio.FIRST_COMPLETED)
        if loop in done:
            waiter.cancel()
            loop.result()
            raise AssertionError("the consumer loop ended before the commit")
        waiter.result()
    finally:
        consumer.running = False
        await asyncio.wait_for(asyncio.gather(loop, return_exceptions=True), 30)
        await consumer.stop()


def test_a_new_group_reads_the_backlog_and_commits_past_the_last_reading(names, tmp_path):
    async def scenario():
        # Produced before the group exists: dec 4's earliest offset is what lets it see them.
        await _produce(names.topic, *(_reading(seq) for seq in range(1, 6)))
        engine = Engine()
        await _run_until_committed(_config(names, tmp_path), engine, 5)
        assert engine.seen == [1, 2, 3, 4, 5]
        assert await _records(names.dlq) == []

    asyncio.run(scenario())


def test_a_worker_killed_before_its_commit_loses_nothing(names, tmp_path):
    log = tmp_path / "seen.log"

    def seen():
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    async def scenario():
        await _produce(names.topic, *(_reading(seq) for seq in (1, 2, 3)))
        worker = subprocess.Popen(
            [sys.executable, os.path.join(HERE, "kafka_crash_worker.py"),
             BOOTSTRAP, names.topic, names.group, str(log), "4"],
        )
        try:
            await _wait_committed(names.group, names.topic, 3)
            assert seen() == [1, 2, 3]
            await _produce(names.topic, _reading(4))
            deadline = time.monotonic() + DEADLINE_S
            while 4 not in seen():
                assert worker.poll() is None, f"the worker exited ({worker.returncode}) before reading 4"
                assert time.monotonic() < deadline, "the worker never received reading 4"
                await asyncio.sleep(0.1)
            # Reading 4 is in flight and uncommitted: kill the process, no shutdown path runs.
            worker.send_signal(signal.SIGKILL)
            worker.wait(10)
        finally:
            if worker.poll() is None:
                worker.kill()
        assert await _committed(names.group, names.topic) == 3

        await _produce(names.topic, _reading(5))
        engine = Engine()
        await _run_until_committed(_config(names, tmp_path), engine, 5)
        # Handed what the dead member had not committed, and nothing it had.
        assert engine.seen == [4, 5]

    asyncio.run(scenario())


def test_an_infrastructure_failure_rewinds_and_the_reading_is_processed_not_dead_lettered(names, tmp_path):
    async def scenario():
        await _produce(names.topic, *(_reading(seq) for seq in (1, 2, 3)))
        engine = Engine(fail=lambda seq, count: ConnectionResetError("db gone") if (seq, count) == (2, 1) else None)
        await _run_until_committed(_config(names, tmp_path), engine, 3)
        counts = collections.Counter(engine.seen)
        assert counts[2] == 2, engine.seen        # failed once, then redelivered from the broker
        assert set(counts) == {1, 2, 3} and engine.seen[-1] == 3, engine.seen
        assert await _records(names.dlq) == []

    asyncio.run(scenario())


def test_malformed_and_invalid_readings_land_on_the_dead_letter_topic(names, tmp_path):
    async def scenario():
        await _produce(names.topic, b"\xffnot json", _reading(1, company_id=None), _reading(2))
        engine = Engine()
        await _run_until_committed(_config(names, tmp_path), engine, 3)
        assert engine.seen == [2]
        letters = [json.loads(r.value) for r in await _records(names.dlq)]
        assert [(l["topic"], l["partition"], l["offset"]) for l in letters] == [
            (names.topic, 0, 0), (names.topic, 0, 1)]
        assert all(l["consumer"] == DEAD_LETTER_CONSUMER for l in letters)
        assert letters[0]["original"] == b"\xffnot json".hex()
        assert "UTF-8 JSON" in letters[0]["reason"]
        assert json.loads(letters[1]["original"])["seq"] == 1
        assert "company_id" in letters[1]["reason"]

    asyncio.run(scenario())


def test_a_reading_whose_retries_run_out_is_dead_lettered_and_the_next_one_processed(names, tmp_path):
    async def scenario():
        await _produce(names.topic, _reading(1), _reading(2))
        engine = Engine(fail=lambda seq, count: RuntimeError("rule blew up") if seq == 1 else None)
        await _run_until_committed(_config(names, tmp_path, KAFKA_MAX_RETRIES=2), engine, 2)
        assert engine.seen == [1, 1, 2]
        letters = [json.loads(r.value) for r in await _records(names.dlq)]
        assert len(letters) == 1 and letters[0]["offset"] == 0
        assert letters[0]["reason"].startswith("retries exhausted") and "rule blew up" in letters[0]["reason"]

    asyncio.run(scenario())
