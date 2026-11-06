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
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import psycopg2  # noqa: E402 - the job image and requirements-dev carry it

pytestmark = pytest.mark.integration

REQUIRE_ENV = "IF_REQUIRE_LIVE_STACK"
ROOT = Path(__file__).resolve().parents[3]
SERVICE = "spark-streaming"
# The job reads at most this many offsets per micro-batch (kafka_to_timescaledb.py), so a backlog of
# several times it spans several batches and a kill can land between two of them.
MAX_OFFSETS_PER_TRIGGER = 10000
# The job's JVM starts, then reads its checkpoint from the object store; every wait is bounded.
DEADLINE_S = 300


def _compose(*args, check=True, **kw):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, check=check, text=True,
                          capture_output=True, **kw)


def _connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "industryflow"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"), connect_timeout=5,
    )


def _wait_for(predicate, what, deadline_s=DEADLINE_S, interval_s=2, context=None):
    """``predicate()`` once it is truthy; on timeout a failure carrying ``context()`` (the job's log)."""
    end = time.monotonic() + deadline_s
    while True:
        value = predicate()
        if value:
            return value
        if time.monotonic() > end:
            detail = f":\n{context()[-4000:]}" if context else ""
            pytest.fail(f"timed out after {deadline_s}s waiting for {what}{detail}")
        time.sleep(interval_s)


@pytest.fixture(scope="module", autouse=True)
def stack():
    try:
        _connect().close()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no stack here"
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"{REQUIRE_ENV}=1 but no database answers: {e}")
        pytest.skip(f"no database reachable: {e}")


@pytest.fixture(scope="module")
def tenant():
    """A tenant provisioned the way the product provisions one, with one equipment."""
    conn = _connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('SET ROLE "api_gateway_user"')
    cur.execute("SELECT * FROM public.provision_company(%s)", (f"stream-proof-{uuid.uuid4().hex[:8]}",))
    company_id = str(cur.fetchone()[0])
    cur.execute("RESET ROLE")
    schema = f"tenant_{company_id.replace('-', '_')}"
    cur.execute(f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
                "VALUES ('pump','unit-1',1) RETURNING equipment_id")
    equipment_id = str(cur.fetchone()[0])
    conn.close()
    yield {"company_id": company_id, "schema": schema, "equipment_id": equipment_id}
    conn = _connect()
    conn.autocommit = True
    cur = conn.cursor()
    suffix = schema[len("tenant_"):]
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
    cur.execute("DELETE FROM public.companies WHERE company_id = %s", (company_id,))
    conn.close()


class Stream:
    """One source topic, its dead-letter topic, one checkpoint and one sensor, and the job over them."""

    def __init__(self, tenant):
        self.tenant = tenant
        tag = uuid.uuid4().hex[:12]
        self.topic = f"if-stream-it-{tag}"
        self.dlq = f"{self.topic}.dlq"
        self.checkpoint = f"s3a://spark-checkpoints/it-{tag}"
        self.container = f"if-stream-it-{tag}"
        self.base = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=1)
        self.runs = 0
        conn = _connect()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'INSERT INTO "{tenant["schema"]}".sensors (equipment_id, sensor_name, sensor_type, position) '
                        "VALUES (%s,%s,'temp',1) RETURNING sensor_id", (tenant["equipment_id"], tag))
            self.sensor_id = str(cur.fetchone()[0])
        conn.close()
        for topic in (self.topic, self.dlq):
            _compose("exec", "-T", "kafka", "kafka-topics", "--bootstrap-server", "localhost:9092",
                     "--create", "--topic", topic, "--partitions", "2", "--replication-factor", "1")

    def reading(self, seq, **over):
        reading = {
            "timestamp": (self.base + timedelta(milliseconds=10 * seq)).isoformat(),
            "sensor_id": self.sensor_id, "equipment_id": self.tenant["equipment_id"],
            "site_id": "site-1", "company_id": self.tenant["company_id"],
            "value": float(seq), "unit": "C", "quality_code": 1,
        }
        reading.update(over)
        return json.dumps(reading)

    def send(self, lines):
        _compose("exec", "-T", "kafka", "kafka-console-producer", "--bootstrap-server", "localhost:9092",
                 "--topic", self.topic, "--producer-property", "acks=all",
                 input="".join(f"{line}\n" for line in lines))

    def start(self):
        """Run the job's image, command and master as deployed, on this stream's names."""
        self.runs += 1
        _compose("run", "-d", "--no-deps", "--name", f"{self.container}-{self.runs}",
                 "-e", f"KAFKA_TOPIC_SENSOR_DATA={self.topic}",
                 "-e", f"KAFKA_TOPIC_MEASUREMENTS_DLQ={self.dlq}",
                 "-e", f"CHECKPOINT_LOCATION={self.checkpoint}",
                 SERVICE)

    @property
    def current(self):
        return f"{self.container}-{self.runs}"

    def state(self):
        out = subprocess.run(["docker", "inspect", "-f", "{{.State.Status}} {{.State.ExitCode}}", self.current],
                             text=True, capture_output=True, check=True).stdout.split()
        return out[0], int(out[1])

    def logs(self, run=None):
        name = f"{self.container}-{run or self.runs}"
        done = subprocess.run(["docker", "logs", name], text=True, capture_output=True, check=True)
        return done.stdout + done.stderr

    def kill(self):
        subprocess.run(["docker", "kill", "-s", "KILL", self.current], check=True, capture_output=True)

    def wait(self, predicate, what, **kw):
        return _wait_for(predicate, what, context=self.logs if self.runs else None, **kw)

    def remove(self):
        for run in range(1, self.runs + 1):
            subprocess.run(["docker", "rm", "-f", f"{self.container}-{run}"], capture_output=True)
        for topic in (self.topic, self.dlq):
            _compose("exec", "-T", "kafka", "kafka-topics", "--bootstrap-server", "localhost:9092",
                     "--delete", "--topic", topic, check=False)

    def _select(self, what):
        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(f'SELECT {what} FROM "{self.tenant["schema"]}".sensor_measurements WHERE sensor_id = %s',
                            (self.sensor_id,))
                return cur.fetchall()
        finally:
            conn.close()

    def count(self):
        return self._select("count(*)")[0][0]

    def values(self):
        """Every written value, sorted: the readings carry their sequence number as the value."""
        return sorted(r[0] for r in self._select("value"))

    def wait_rows(self, n):
        def reached():
            if self.state()[0] != "running":
                pytest.fail(f"the job stopped before writing {n} rows:\n{self.logs()[-4000:]}")
            return self.count() >= n
        self.wait(reached, f"{n} rows in {self.tenant['schema']}.sensor_measurements")

    def dead_letters(self, expected):
        def read():
            done = _compose("exec", "-T", "kafka", "kafka-console-consumer", "--bootstrap-server",
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

    _compose("stop", "timescaledb")
    try:
        stream.send(stream.reading(i) for i in range(1, 201))
        stream.wait(lambda: stream.state()[0] == "exited", "the job to fail its batch while the database is down")
        _, code = stream.state()
        assert code != 0, "the job stopped cleanly while its batch could not be written"
    finally:
        _compose("start", "timescaledb")
        _wait_for(_database_answers, "the database to return")

    stream.start()
    stream.wait_rows(201)
    assert stream.values() == [float(i) for i in range(201)]


def _database_answers():
    try:
        _connect().close()
        return True
    except Exception:  # noqa: BLE001
        return False


def test_a_write_the_database_refuses_fails_the_batch_and_advances_no_offset(stream):
    """The lookup succeeds and the executors' write is refused: the write path's own re-raise."""
    stream.send([stream.reading(0)])
    stream.start()
    stream.wait_rows(1)

    table = f'"{stream.tenant["schema"]}".sensor_measurements'
    trigger = f"if_stream_it_refuse_{stream.sensor_id.replace('-', '_')}"
    conn = _connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f"CREATE FUNCTION public.{trigger}() RETURNS trigger LANGUAGE plpgsql AS "
                "$$BEGIN RAISE EXCEPTION 'refused by the proof'; END$$")
    try:
        cur.execute(f"CREATE TRIGGER {trigger} BEFORE INSERT ON {table} FOR EACH ROW "
                    f"WHEN (NEW.sensor_id = '{stream.sensor_id}') EXECUTE FUNCTION public.{trigger}()")
        stream.send(stream.reading(i) for i in range(1, 51))
        stream.wait(lambda: stream.state()[0] == "exited", "the job to fail its refused write")
        _, code = stream.state()
        assert code != 0, "the job stopped cleanly while its write was refused"
        assert "refused by the proof" in stream.logs(), "the batch failed, but not on the refused write"
        assert stream.count() == 1
    finally:
        cur.execute(f"DROP TRIGGER IF EXISTS {trigger} ON {table}")
        cur.execute(f"DROP FUNCTION IF EXISTS public.{trigger}()")
        conn.close()

    stream.start()
    stream.wait_rows(51)
    assert stream.values() == [float(i) for i in range(51)]
