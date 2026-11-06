# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The aggregation job against the platform's own broker, database and checkpoint store (ADR-0005
dec 2-3, ADR-0006 dec 1-6, ADR-0026 dec 11).

test_aggregations.py runs the windowing on a file source with the writer injected: it proves the
arithmetic, not what the broker, the database and the checkpointed window state do with it. Here the
job's own image runs its own ``__main__`` (``docker compose run spark-aggregations``) on its deployed
master and worker, on a topic, checkpoint and sensors of its own, and each outcome is read back from
the tenant's ``sensor_aggregations_1min`` table. The job starts at the newest offset, so each test
first primes it: a probe sensor's readings are sent until one of its windows is written. Event times
only rise from there, because the watermark is the query's, not the sensor's.

- a window is written when the watermark passes its end, with its statistics (dec 1-2), and a
  reading behind the watermark changes no written window;
- a reading far in the future, or of a sensor the tenant does not know, is excluded and does not move
  the watermark or fail the batch (dec 4);
- a window still open is not written, and a job killed with it open resumes its window state from the object-store checkpoint: the
  window is written once, with every reading counted once (dec 3, 5; ADR-0005 dec 2);
- a database that cannot be reached, or a write it refuses, fails the batch and the job exits
  (dec 4, 6); the window is written once the job runs again (ADR-0005 dec 3).

Needs what test_streaming_integration.py needs, with the built spark-aggregations image and room on
the worker for its application (on a host that also runs the deployed jobs, stop one of them first).
"""
from datetime import datetime, timedelta, timezone

import pytest

import spark_stack
from industryflow_contracts import windows
from spark_stack import DatabaseDown, Job, RefusedWrites

pytestmark = pytest.mark.integration

REQUIRE_ENV = spark_stack.REQUIRE_ENV
GRANULARITY = windows.window("1min")
WIDTH = timedelta(seconds=GRANULARITY.width_seconds)
LATENESS = timedelta(seconds=GRANULARITY.lateness_seconds)


@pytest.fixture(scope="module", autouse=True)
def stack():
    spark_stack.require_stack()


@pytest.fixture(scope="module")
def tenant():
    t = spark_stack.provision_tenant("agg-proof")
    yield t
    spark_stack.drop_tenant(t)


class Aggregations(Job):
    """The aggregation job over a topic and checkpoint of its own; reads its 1-minute table back."""

    def __init__(self, tenant):
        super().__init__(tenant, "spark-aggregations", "if-agg-it")
        self.table = f'"{tenant["schema"]}"."{GRANULARITY.table}"'
        self.probe = self.add_sensor(f"{self.tag}-probe")
        # Window-aligned and far enough back that every event time the test sends stays in the past.
        now = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        self.clock = now - timedelta(hours=3)

    def window(self, start):
        """The ``time`` the job writes for the window starting at ``start``: its end."""
        return start + WIDTH

    def closer(self, sensor_id, window_start):
        """A reading of ``sensor_id`` that moves the watermark past the end of ``window_start``'s window."""
        return self.reading(sensor_id, window_start + WIDTH + LATENESS, 0)

    def rows(self, sensor_id):
        """``{window end: (avg, min, max, count)}`` written for ``sensor_id``."""
        return {r[0]: tuple(r[1:]) for r in self.select(
            f"SELECT time, avg_value, min_value, max_value, count_values FROM {self.table} WHERE sensor_id = %s",
            (sensor_id,))}

    def prime(self):
        """Probe readings, one window apart, until one of the probe's windows is written: the source is
        positioned. Returns the first window start after every probe event time."""
        def written():
            at = self.clock
            self.clock += WIDTH
            self.send([self.reading(self.probe, at, 1)])
            return bool(self.rows(self.probe))
        self.wait_running(written, "the job to write a probe window (its source positioned)", interval_s=3)
        start = self.clock + LATENESS + WIDTH
        self.clock = start
        return start

    def wait_window(self, sensor_id, window_start):
        end = self.window(window_start)
        return self.wait_running(lambda: self.rows(sensor_id).get(end), f"the window ending {end:%H:%M} of {sensor_id}")


@pytest.fixture
def job(tenant):
    j = Aggregations(tenant)
    yield j
    j.remove()


def _at(start, seconds):
    return start + timedelta(seconds=seconds)


def test_a_window_is_written_once_the_watermark_passes_it_and_a_late_reading_changes_nothing(job):
    s = job.add_sensor(f"{job.tag}-s")
    job.start()
    w1 = job.prime()
    w2 = w1 + WIDTH
    job.send([job.reading(s, _at(w1, 10 * i), v) for i, v in enumerate([1, 2, 3, 4, 5, 6])]
             + [job.reading(s, _at(w2, 5), 10), job.reading(s, _at(w2, 15), 20)])
    job.send([job.closer(s, w2)])
    assert job.wait_window(s, w2) == (15.0, 10.0, 20.0, 2)
    assert job.rows(s)[job.window(w1)] == (3.5, 1.0, 6.0, 6)

    # A reading for the written window, now behind the watermark, then a later window to close.
    w3 = w2 + 5 * WIDTH  # past the window the closer itself opened
    job.send([job.reading(s, _at(w1, 1), 1000), job.reading(s, _at(w3, 1), 7), job.closer(s, w3)])
    assert job.wait_window(s, w3) == (7.0, 7.0, 7.0, 1)
    assert job.rows(s)[job.window(w1)] == (3.5, 1.0, 6.0, 6), "a late reading rewrote a closed window"


def test_a_future_or_unknown_reading_is_excluded_without_moving_the_watermark(job):
    s = job.add_sensor(f"{job.tag}-s")
    unknown = "00000000-0000-4000-8000-000000000001"
    job.start()
    w1 = job.prime()
    far = datetime.now(timezone.utc) + timedelta(days=1)
    job.send([job.reading(s, _at(w1, 5), 4), job.reading(unknown, _at(w1, 6), 99),
              job.reading(s, far, 99), job.reading(s, _at(w1, 7), 6)])
    # Had the future reading moved the watermark, this window's reading would already be late.
    w2 = w1 + 2 * WIDTH
    job.send([job.reading(s, _at(w2, 5), 8), job.closer(s, w2)])
    assert job.wait_window(s, w2) == (8.0, 8.0, 8.0, 1)
    assert job.rows(s)[job.window(w1)] == (5.0, 4.0, 6.0, 2)
    assert not job.rows(unknown), "an aggregate of a sensor the tenant does not know was written"


def test_a_killed_job_resumes_its_open_window_and_counts_each_reading_once(job):
    s = job.add_sensor(f"{job.tag}-s")
    job.start()
    w1 = job.prime()
    open_window = w1 + 2 * WIDTH
    job.send([job.reading(s, _at(open_window, 10 * i), i) for i in range(6)])
    # Closes w1 (empty for s) by way of a probe window, but not the open one: once that probe window
    # is written, the batch that holds the open window's readings has run.
    job.send([job.reading(job.probe, _at(w1, 1), 1), job.closer(job.probe, w1)])
    job.wait_window(job.probe, w1)
    assert job.window(open_window) not in job.rows(s)
    job.kill()

    job.start()
    job.send([job.closer(s, open_window)])
    assert job.wait_window(s, open_window) == (2.5, 0.0, 5.0, 6), \
        "the open window lost or recounted readings across the restart: its state was not resumed"


def test_a_database_outage_fails_the_batch_and_the_window_is_written_on_restart(job):
    s = job.add_sensor(f"{job.tag}-s")
    job.start()
    w1 = job.prime()
    with DatabaseDown():
        job.send([job.reading(s, _at(w1, 5), 3), job.reading(s, _at(w1, 25), 5), job.closer(s, w1)])
        job.wait_exited("the job to fail its batch while the database is down")
    job.start()
    assert job.wait_window(s, w1) == (4.0, 3.0, 5.0, 2)


def test_a_refused_write_fails_the_batch_and_the_window_is_written_on_restart(job):
    s = job.add_sensor(f"{job.tag}-s")
    job.start()
    w1 = job.prime()
    with RefusedWrites(job.table, s):
        job.send([job.reading(s, _at(w1, 5), 3), job.reading(s, _at(w1, 25), 5), job.closer(s, w1)])
        job.wait_exited("the job to fail its refused write")
        assert RefusedWrites.MESSAGE in job.logs(), "the batch failed, but not on the refused write"
        assert not job.rows(s)
    job.start()
    assert job.wait_window(s, w1) == (4.0, 3.0, 5.0, 2)
