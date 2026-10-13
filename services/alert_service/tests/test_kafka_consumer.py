# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert worker's consumer loop: commit, rewind, dead-letter routing, and loop death (ADR-0005).

The Kafka client is faked at the boundary the loop uses (getmany / commit / committed / seek /
send_and_wait), so these run without a broker. Each scenario is a script of batches; the loop is
stopped when the script runs out.
"""
import asyncio
import json
import os
import sys
from collections import namedtuple
from types import SimpleNamespace

import asyncpg
import pytest
from aiokafka import TopicPartition
from aiokafka.errors import CommitFailedError, KafkaError

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))

import kafka_consumer  # noqa: E402
from errors import MessageValidationError, is_infrastructure_error  # noqa: E402
from kafka_consumer import AlertKafkaConsumer  # noqa: E402

CO = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
SENSOR = "11111111-1111-1111-1111-111111111111"
TP = TopicPartition("sensor-data-raw", 0)
Record = namedtuple("Record", "topic partition offset value")


def _record(offset, payload):
    raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return Record(TP.topic, TP.partition, offset, raw)


def _reading(**over):
    reading = {"company_id": CO, "sensor_id": SENSOR, "value": 1.0}
    reading.update(over)
    return reading


class FakeConsumer:
    def __init__(self, batches, owner):
        self.batches = list(batches)
        self.owner = owner
        self.commits = []
        self.commit_errors = []
        self.committed_offset = None
        self.seeks = []

    async def getmany(self, timeout_ms=None, max_records=None):
        if not self.batches:
            self.owner.running = False
            return {}
        batch = self.batches.pop(0)
        if isinstance(batch, BaseException):
            raise batch
        return {TP: batch}

    async def commit(self, offsets=None):
        if self.commit_errors:
            raise self.commit_errors.pop(0)
        self.commits.append(dict(offsets))
        self.committed_offset = offsets[TP]

    def assignment(self):
        return {TP}

    async def committed(self, tp):
        return self.committed_offset

    def seek(self, tp, offset):
        self.seeks.append(offset)

    async def seek_to_beginning(self, tp):
        self.seeks.append("beginning")


class FakeProducer:
    def __init__(self):
        self.sent = []
        self.fail = False

    async def send_and_wait(self, topic, value=None):
        if self.fail:
            raise OSError("broker unavailable")
        self.sent.append((topic, value))


class ScriptedEngine:
    """evaluate() raises the next scripted error (if any), else records the reading."""

    def __init__(self, errors=()):
        self.errors = list(errors)
        self.seen = []

    async def evaluate(self, sensor_data, company_id):
        if self.errors:
            raise self.errors.pop(0)
        self.seen.append(sensor_data)
        return []


@pytest.fixture
def sleeps(monkeypatch):
    delays = []

    async def fake_sleep(delay, *a, **k):
        delays.append(delay)

    monkeypatch.setattr(kafka_consumer.asyncio, "sleep", fake_sleep)
    return delays


def _consumer(batches, engine, tmp_path, **cfg):
    config = SimpleNamespace(
        KAFKA_MAX_RETRIES=3, KAFKA_RETRY_BACKOFF_SECONDS=1.0, KAFKA_MAX_BACKOFF_SECONDS=30.0,
        KAFKA_TOPIC_DLQ="sensor-data-raw.dlq", HEARTBEAT_FILE=str(tmp_path / "heartbeat"),
    )
    for k, v in cfg.items():
        setattr(config, k, v)
    c = AlertKafkaConsumer(config, engine)
    c.consumer = FakeConsumer(batches, c)
    c.producer = FakeProducer()
    c.running = True
    return c


def _run(c):
    asyncio.run(c.consume_messages())


# --- commit ---------------------------------------------------------------------------------------

def test_a_handled_batch_commits_past_its_last_offset(tmp_path, sleeps):
    engine = ScriptedEngine()
    c = _consumer([[_record(5, _reading()), _record(6, _reading())]], engine, tmp_path)
    _run(c)
    assert len(engine.seen) == 2
    assert c.consumer.commits == [{TP: 7}]
    assert c.producer.sent == []


# --- dead-letter routing --------------------------------------------------------------------------

def test_malformed_record_is_dead_lettered_and_the_batch_still_commits(tmp_path, sleeps):
    engine = ScriptedEngine()
    c = _consumer([[_record(0, b"{not json"), _record(1, _reading())]], engine, tmp_path)
    _run(c)
    assert [t for t, _ in c.producer.sent] == ["sensor-data-raw.dlq"]
    dead = c.producer.sent[0][1]
    assert dead["offset"] == 0 and dead["original"] == "{not json" and "JSON" in dead["error"]
    assert len(engine.seen) == 1
    assert c.consumer.commits == [{TP: 2}]


def test_reading_without_company_id_is_dead_lettered_not_silently_committed(tmp_path, sleeps):
    engine = ScriptedEngine()
    c = _consumer([[_record(0, {"sensor_id": SENSOR, "value": 1.0})]], engine, tmp_path)
    _run(c)
    assert len(c.producer.sent) == 1
    assert "company_id" in c.producer.sent[0][1]["error"]
    assert engine.seen == []
    assert c.consumer.commits == [{TP: 1}]


def test_validation_error_from_the_engine_is_dead_lettered_without_retry(tmp_path, sleeps):
    engine = ScriptedEngine([MessageValidationError("sensor_id is not a UUID: 'x'")])
    c = _consumer([[_record(0, _reading())]], engine, tmp_path)
    _run(c)
    assert len(c.producer.sent) == 1
    assert sleeps == []  # no retry backoff for a reading no retry can fix
    assert c.consumer.commits == [{TP: 1}]


def test_unknown_error_is_retried_then_dead_lettered(tmp_path, sleeps):
    engine = ScriptedEngine([KeyError("boom")] * 3)
    c = _consumer([[_record(0, _reading())]], engine, tmp_path)
    _run(c)
    assert len(c.producer.sent) == 1
    assert "retries exhausted" in c.producer.sent[0][1]["error"]
    assert sleeps == [1.0, 2.0]
    assert c.consumer.commits == [{TP: 1}]


# --- infrastructure errors: never dead-lettered, rewound and retried ------------------------------

@pytest.mark.parametrize("error", [
    asyncpg.exceptions.ConnectionDoesNotExistError("connection was closed"),
    asyncpg.exceptions.TooManyConnectionsError("too many connections"),
    asyncpg.exceptions.CannotConnectNowError("the database system is starting up"),
    ConnectionRefusedError("refused"),
    TimeoutError(),
])
def test_infrastructure_error_rewinds_and_retries_instead_of_dead_lettering(tmp_path, sleeps, error):
    engine = ScriptedEngine([error])
    rec = _record(3, _reading())
    c = _consumer([[rec], [rec]], engine, tmp_path)
    c.consumer.committed_offset = 3
    _run(c)
    assert c.producer.sent == []           # the reading is fine; it is never dead-lettered
    assert c.consumer.seeks == [3]         # rewound to the last commit
    assert len(engine.seen) == 1           # redelivered and processed once the DB is back
    assert c.consumer.commits == [{TP: 4}]


def test_infrastructure_backoff_doubles_and_is_capped(tmp_path, sleeps):
    outage = [OSError("db down")] * 7
    rec = _record(0, _reading())
    c = _consumer([[rec]] * 8, ScriptedEngine(outage), tmp_path, KAFKA_MAX_BACKOFF_SECONDS=10.0)
    _run(c)
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 10.0, 10.0, 10.0]
    assert c.consumer.commits == [{TP: 1}]


def test_dead_letter_failure_does_not_advance_the_offset(tmp_path, sleeps):
    c = _consumer([[_record(0, b"\xff")]], ScriptedEngine(), tmp_path)
    c.producer.fail = True
    _run(c)
    assert c.consumer.commits == []
    assert c.consumer.seeks == ["beginning"]


def test_constraint_violation_is_not_an_infrastructure_error():
    assert not is_infrastructure_error(asyncpg.exceptions.UniqueViolationError("dup"))
    assert not is_infrastructure_error(MessageValidationError("bad"))
    assert is_infrastructure_error(asyncpg.exceptions.DeadlockDetectedError("deadlock"))


# --- commit failures and loop death ---------------------------------------------------------------

def test_commit_lost_to_a_rebalance_rewinds_and_the_loop_carries_on(tmp_path, sleeps):
    engine = ScriptedEngine()
    rec = _record(0, _reading())
    c = _consumer([[rec], [rec]], engine, tmp_path)
    c.consumer.commit_errors = [CommitFailedError("group rebalanced")]
    _run(c)
    assert c.consumer.seeks == ["beginning"]
    assert c.consumer.commits == [{TP: 1}]  # the redelivered batch commits
    assert len(engine.seen) == 2            # processed twice; alert writes are idempotent


def test_unexpected_commit_error_propagates(tmp_path, sleeps):
    c = _consumer([[_record(0, _reading())]], ScriptedEngine(), tmp_path)
    c.consumer.commit_errors = [KafkaError("unexpected")]
    with pytest.raises(KafkaError):
        _run(c)


def test_unexpected_error_from_getmany_ends_the_loop_loudly(tmp_path, sleeps):
    """The loop used to log 'Fatal error' and return, leaving a live process consuming nothing."""
    c = _consumer([RuntimeError("consumer is broken")], ScriptedEngine(), tmp_path)
    with pytest.raises(RuntimeError, match="consumer is broken"):
        _run(c)


def test_every_poll_touches_the_heartbeat(tmp_path, sleeps):
    c = _consumer([[_record(0, _reading())]], ScriptedEngine(), tmp_path)
    hb = tmp_path / "heartbeat"
    _run(c)
    assert hb.exists()
    before = hb.stat().st_mtime
    os.utime(hb, (before - 600, before - 600))
    c.consumer.batches = [[_record(1, _reading())]]
    c.running = True
    _run(c)
    assert hb.stat().st_mtime > before - 600
