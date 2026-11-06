# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The streaming job against the platform's own broker, database and checkpoint store (ADR-0005
dec 2-3, ADR-0006 dec 3-6, ADR-0026 dec 11).

test_streaming_batch.py injects the lookup, the dead-letter publisher and the writer: it proves the
routing, not what the broker, the database and the checkpoint do with it. Here the job's own image
runs its own ``main()`` (``docker compose run spark-streaming``) on its deployed master and worker,
reading a topic of its own from the earliest offset, checkpointing to the object store and writing
over verified TLS as its own role. Each outcome is read back from the store it lands in:

- a backlog written before the job starts is kept, every reading once (the earliest start), and
  each unwritable reading lands on the dead-letter topic with its reason, never in the table;
- a job killed mid-stream resumes from its object-store checkpoint, not from the start (ADR-0006
  dec 5), and loses nothing (dec 2); the table's natural key, not the job, is what keeps a replayed
  reading single (ADR-0006 dec 3);
- a database that cannot be reached, or a write the database refuses, fails the batch and the job
  exits (ADR-0006 dec 4, 6) with no offset advanced: the readings of the failed batch are written
  once the job runs again (dec 3).

Needs the compose stack's kafka, timescaledb (migrated), minio (initialised), spark-master and a
spark-worker with a free core and executor's memory for one more application (on a host that also
runs the deployed jobs, stop one of them first), and the built spark-streaming image. The outage
test stops the database. It skips when the database is not reachable; IF_REQUIRE_LIVE_STACK=1, set
by the CI job that starts them, turns that skip into a failure.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone

import pytest

import spark_stack
from spark_stack import DatabaseDown, Job, RefusedWrites

pytestmark = pytest.mark.integration

REQUIRE_ENV = spark_stack.REQUIRE_ENV
# The job reads at most this many offsets per micro-batch (kafka_to_timescaledb.py), so a backlog of
# several times it spans several batches and a kill can land between two of them.
MAX_OFFSETS_PER_TRIGGER = 10000


@pytest.fixture(scope="module", autouse=True)
def stack():
    spark_stack.require_stack()


@pytest.fixture(scope="module")
def tenant():
    t = spark_stack.provision_tenant("stream-proof")
    yield t
    spark_stack.drop_tenant(t)


class Stream(Job):
    """The streaming job over a topic, checkpoint and sensor of its own; reads its raw table back."""

    def __init__(self, tenant):
        super().__init__(tenant, "spark-streaming", "if-stream-it")
        self.base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
        self.sensor_id = self.add_sensor(self.tag)

    def reading(self, seq, **over):
        return super().reading(self.sensor_id, self.base + timedelta(milliseconds=10 * seq), seq, **over)

    def _select(self, what):
        return self.select(f'SELECT {what} FROM "{self.tenant["schema"]}".sensor_measurements WHERE sensor_id = %s',
                           (self.sensor_id,))

    def count(self):
        return self._select("count(*)")[0][0]

    def values(self):
        """Every written value, sorted: the readings carry their sequence number as the value."""
        return sorted(r[0] for r in self._select("value"))

    def wait_rows(self, n):
        self.wait_running(lambda: self.count() >= n, f"{n} rows in {self.tenant['schema']}.sensor_measurements")

    def dead_letters(self, expected):
        import json

        def read():
            done = spark_stack.compose("exec", "-T", "kafka", "kafka-console-consumer", "--bootstrap-server",
                                       "localhost:9092", "--topic", self.dlq, "--from-beginning",
                                       "--timeout-ms", "10000", check=False)
            records = [json.loads(line) for line in done.stdout.splitlines() if line.startswith("{")]
            return records if len(records) >= expected else None
        return self.wait(read, f"{expected} dead letters on {self.dlq}")

    def batch_ids(self, run):
        return [int(b) for b in re.findall(r"Batch (\d+): upserted", self.logs(run))]


@pytest.fixture
def stream(tenant):
    s = Stream(tenant)
    yield s
    s.remove()


def test_a_backlog_is_kept_from_the_earliest_offset_and_unwritable_readings_are_dead_lettered(stream):
    unknown = str(uuid.uuid4())
    invalid = {
        "not json": "missing_or_malformed_field",
        stream.reading(1, company_id="acme"): "invalid_company_id",
        stream.reading(2, company_id=unknown): "unknown_tenant",
        stream.reading(3, sensor_id=unknown): "unknown_sensor",
        stream.reading(4, timestamp=(datetime.now(timezone.utc) + timedelta(days=1)).isoformat()):
            "future_timestamp",
    }
    valid = [stream.reading(i) for i in range(500)]
    stream.send(valid[:250] + list(invalid) + valid[250:])

    stream.start()
    stream.wait_rows(len(valid))

    letters = stream.dead_letters(len(invalid))
    assert {letter["original"]: letter["reason"] for letter in letters} == invalid
    assert {letter["topic"] for letter in letters} == {stream.topic}
    assert stream.values() == [float(i) for i in range(len(valid))]


def test_a_killed_job_resumes_from_its_checkpoint_without_loss_or_duplicate(stream):
    total = 6 * MAX_OFFSETS_PER_TRIGGER
    stream.send(stream.reading(i) for i in range(total))

    stream.start()
    # Batch 1 starts only once batch 0 is committed, so a kill now has a checkpoint to resume from.
    stream.wait(lambda: "Batch 1: upserted" in stream.logs(), "the second batch to be written", interval_s=0.5)
    stream.kill()
    written_at_kill = stream.count()
    assert 0 < written_at_kill < total, "the kill did not land mid-stream; the proof would see nothing"

    stream.send(stream.reading(i) for i in range(total, total + 100))
    stream.start()
    stream.wait_rows(total + 100)

    second = stream.batch_ids(2)
    assert second and min(second) > 0, "the restarted job began again at batch 0: its checkpoint was not read"
    assert stream.values() == [float(i) for i in range(total + 100)]


def test_no_offset_advances_while_the_database_is_down(stream):
    stream.send([stream.reading(0)])
    stream.start()
    stream.wait_rows(1)

    with DatabaseDown():
        stream.send(stream.reading(i) for i in range(1, 201))
        stream.wait_exited("the job to fail its batch while the database is down")

    stream.start()
    stream.wait_rows(201)
    assert stream.values() == [float(i) for i in range(201)]


def test_a_write_the_database_refuses_fails_the_batch_and_advances_no_offset(stream):
    """The lookup succeeds and the executors' write is refused: the write path's own re-raise."""
    stream.send([stream.reading(0)])
    stream.start()
    stream.wait_rows(1)

    with RefusedWrites(f'"{stream.tenant["schema"]}".sensor_measurements', stream.sensor_id):
        stream.send(stream.reading(i) for i in range(1, 51))
        stream.wait_exited("the job to fail its refused write")
        assert RefusedWrites.MESSAGE in stream.logs(), "the batch failed, but not on the refused write"
        assert stream.count() == 1

    stream.start()
    stream.wait_rows(51)
    assert stream.values() == [float(i) for i in range(51)]
