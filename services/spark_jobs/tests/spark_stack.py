# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The compose stack the Spark jobs' real-store proofs run against, and a job run on it.

A proof runs a job's own image, command and master (``docker compose run <service>``) against the
compose broker, database and checkpoint store, on a topic, a checkpoint and a sensor of its own, and
reads each outcome back from the store it lands in. Imported by test_streaming_integration.py and
test_aggregations_integration.py; it is a helper, not a test module.
"""
import json
import os
import subprocess
import time
import uuid
from pathlib import Path

import psycopg2
import pytest

# Set by the CI job that starts the stack: a stack that does not answer fails instead of skipping.
REQUIRE_ENV = "IF_REQUIRE_LIVE_STACK"
ROOT = Path(__file__).resolve().parents[3]
# A job's JVM starts, then reads its checkpoint from the object store; every wait is bounded.
DEADLINE_S = 300


def compose(*args, check=True, **kw):
    return subprocess.run(["docker", "compose", *args], cwd=ROOT, check=check, text=True,
                          capture_output=True, **kw)


def connect():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "industryflow"), user=os.getenv("PGUSER", "postgres"),
        password=os.getenv("PGPASSWORD", "postgres"), connect_timeout=5,
    )


def database_answers():
    try:
        connect().close()
        return True
    except Exception:  # noqa: BLE001 - any connection failure means "not answering"
        return False


def wait_for(predicate, what, deadline_s=DEADLINE_S, interval_s=2, context=None):
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


def require_stack():
    """Skip when no database answers, or fail when ``IF_REQUIRE_LIVE_STACK=1``."""
    try:
        connect().close()
    except Exception as e:  # noqa: BLE001 - any connection failure means "no stack here"
        if os.environ.get(REQUIRE_ENV) == "1":
            pytest.fail(f"{REQUIRE_ENV}=1 but no database answers: {e}")
        pytest.skip(f"no database reachable: {e}")


def provision_tenant(name_prefix):
    """A tenant provisioned the way the product provisions one, with one equipment."""
    conn = connect()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute('SET ROLE "api_gateway_user"')
    cur.execute("SELECT * FROM public.provision_company(%s)", (f"{name_prefix}-{uuid.uuid4().hex[:8]}",))
    company_id = str(cur.fetchone()[0])
    cur.execute("RESET ROLE")
    schema = f"tenant_{company_id.replace('-', '_')}"
    cur.execute(f'INSERT INTO "{schema}".equipment (equipment_type, name, sensor_count) '
                "VALUES ('pump','unit-1',1) RETURNING equipment_id")
    equipment_id = str(cur.fetchone()[0])
    conn.close()
    return {"company_id": company_id, "schema": schema, "equipment_id": equipment_id}


def drop_tenant(tenant):
    conn = connect()
    conn.autocommit = True
    cur = conn.cursor()
    schema = tenant["schema"]
    suffix = schema[len("tenant_"):]
    cur.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_sqlproxy_{suffix}"')
    cur.execute(f'DROP ROLE IF EXISTS "tenant_reader_{suffix}"')
    cur.execute("DELETE FROM public.companies WHERE company_id = %s", (tenant["company_id"],))
    conn.close()


class Job:
    """One compose service run over a source topic, a dead-letter topic and a checkpoint of its own."""

    def __init__(self, tenant, service, tag_prefix):
        self.tenant = tenant
        self.service = service
        tag = uuid.uuid4().hex[:12]
        self.tag = tag
        self.topic = f"{tag_prefix}-{tag}"
        # "-dlq", never ".dlq": the platform's dead-letter alert watches every ".dlq" topic.
        self.dlq = f"{self.topic}-dlq"
        self.checkpoint = f"s3a://spark-checkpoints/it-{tag}"
        self.container = self.topic
        self.runs = 0
        for topic in (self.topic, self.dlq):
            compose("exec", "-T", "kafka", "kafka-topics", "--bootstrap-server", "localhost:9092",
                    "--create", "--topic", topic, "--partitions", "2", "--replication-factor", "1")

    def add_sensor(self, name):
        conn = connect()
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'INSERT INTO "{self.tenant["schema"]}".sensors (equipment_id, sensor_name, sensor_type, position) '
                        "VALUES (%s,%s,'temp',1) RETURNING sensor_id", (self.tenant["equipment_id"], name))
            sensor_id = str(cur.fetchone()[0])
        conn.close()
        return sensor_id

    def reading(self, sensor_id, at, value, /, **over):
        reading = {
            "timestamp": at.isoformat(), "sensor_id": sensor_id,
            "equipment_id": self.tenant["equipment_id"], "site_id": "site-1",
            "company_id": self.tenant["company_id"], "value": float(value), "unit": "C", "quality_code": 1,
        }
        reading.update(over)
        return json.dumps(reading)

    def send(self, lines):
        compose("exec", "-T", "kafka", "kafka-console-producer", "--bootstrap-server", "localhost:9092",
                "--topic", self.topic, "--producer-property", "acks=all",
                input="".join(f"{line}\n" for line in lines))

    def start(self):
        """Run the job's image, command and master as deployed, on this job's names."""
        self.runs += 1
        compose("run", "-d", "--no-deps", "--name", f"{self.container}-{self.runs}",
                "-e", f"KAFKA_TOPIC_SENSOR_DATA={self.topic}",
                "-e", f"KAFKA_TOPIC_MEASUREMENTS_DLQ={self.dlq}",
                "-e", f"CHECKPOINT_LOCATION={self.checkpoint}",
                self.service)

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
        return wait_for(predicate, what, context=self.logs if self.runs else None, **kw)

    def wait_running(self, predicate, what, **kw):
        """``wait``, failing at once if the job stops first."""
        def check():
            if self.state()[0] != "running":
                pytest.fail(f"the job stopped before {what}:\n{self.logs()[-4000:]}")
            return predicate()
        return self.wait(check, what, **kw)

    def wait_exited(self, what):
        self.wait(lambda: self.state()[0] == "exited", what)
        _, code = self.state()
        assert code != 0, f"the job stopped cleanly: {what}"

    def remove(self):
        for run in range(1, self.runs + 1):
            subprocess.run(["docker", "rm", "-f", f"{self.container}-{run}"], capture_output=True)
        for topic in (self.topic, self.dlq):
            compose("exec", "-T", "kafka", "kafka-topics", "--bootstrap-server", "localhost:9092",
                    "--delete", "--topic", topic, check=False)

    def select(self, sql, args=()):
        conn = connect()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, args)
                return cur.fetchall()
        finally:
            conn.close()


class RefusedWrites:
    """A trigger that makes the database refuse every insert of one sensor's rows into one table."""

    MESSAGE = "refused by the proof"

    def __init__(self, table, sensor_id):
        self.table, self.sensor_id = table, sensor_id
        self.name = f"if_stream_it_refuse_{sensor_id.replace('-', '_')}"

    def __enter__(self):
        self.conn = connect()
        self.conn.autocommit = True
        cur = self.conn.cursor()
        cur.execute(f"CREATE FUNCTION public.{self.name}() RETURNS trigger LANGUAGE plpgsql AS "
                    f"$$BEGIN RAISE EXCEPTION '{self.MESSAGE}'; END$$")
        cur.execute(f"CREATE TRIGGER {self.name} BEFORE INSERT ON {self.table} FOR EACH ROW "
                    f"WHEN (NEW.sensor_id = '{self.sensor_id}') EXECUTE FUNCTION public.{self.name}()")
        return self

    def __exit__(self, *exc):
        cur = self.conn.cursor()
        cur.execute(f"DROP TRIGGER IF EXISTS {self.name} ON {self.table}")
        cur.execute(f"DROP FUNCTION IF EXISTS public.{self.name}()")
        self.conn.close()
        return False


class DatabaseDown:
    """The compose database stopped for the block, and answering again after it."""

    def __enter__(self):
        compose("stop", "timescaledb")
        return self

    def __exit__(self, *exc):
        compose("start", "timescaledb")
        wait_for(database_answers, "the database to return")
        return False
