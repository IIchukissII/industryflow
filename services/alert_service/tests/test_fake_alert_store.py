# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""FakeAlertStore decides every shared dedup scenario as save_alert_once's SQL must.

The same table (dedup_scenarios.py) runs on a real TimescaleDB in
infrastructure/timescaledb/tests/test_alert_dedup_hypertable.py, so a scenario passing here and there
is the fake and the SQL agreeing. A line of the fake no scenario reaches is a behaviour the live
suite never checked, so it fails here.
"""
import asyncio
import dis
import inspect
import os
import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, os.path.dirname(__file__))

import fake_alert_store  # noqa: E402
from dedup_scenarios import SCENARIOS, run  # noqa: E402
from fake_alert_store import FakeAlertStore  # noqa: E402

BASE = datetime(2026, 10, 11, 12, 0, tzinfo=timezone.utc)
CO = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[sc.name for sc in SCENARIOS])
def test_the_fake_decides_the_scenario_as_the_sql_does(scenario):
    got, want = asyncio.run(run(FakeAlertStore(), scenario, BASE, CO))
    assert got == want


def test_scenario_names_are_unique():
    names = [sc.name for sc in SCENARIOS]
    assert len(names) == len(set(names))


def _code_objects(cls):
    funcs = [getattr(f, "__func__", f) for f in vars(cls).values()]
    todo = [f.__code__ for f in funcs if inspect.isfunction(f)]
    while todo:
        code = todo.pop()
        yield code
        todo += [c for c in code.co_consts if inspect.iscode(c)]


def test_every_line_of_the_fake_is_reached_by_a_scenario():
    codes = set(_code_objects(FakeAlertStore))
    assert codes, "found no methods on FakeAlertStore — revisit this guard"
    lines = {line for code in codes for _, _, line in code.co_lines()
             if line is not None and line != code.co_firstlineno}
    lines -= {i.positions.lineno for code in codes for i in dis.get_instructions(code)
              if i.opname == "RESUME"}
    reached = set()
    path = fake_alert_store.__file__

    def trace(frame, event, arg):
        if frame.f_code.co_filename != path:
            return None
        if event == "line":
            reached.add(frame.f_lineno)
        return trace

    sys.settrace(trace)
    try:
        for scenario in SCENARIOS:
            asyncio.run(run(FakeAlertStore(), scenario, BASE, CO))
    finally:
        sys.settrace(None)
    source = inspect.getsource(fake_alert_store).splitlines()
    unreached = [f"{n}: {source[n - 1].strip()}" for n in sorted(lines - reached)]
    assert not unreached, (
        "lines of FakeAlertStore no dedup scenario reaches; add a scenario to dedup_scenarios.py so "
        "the live suite checks it against the SQL:\n" + "\n".join(unreached))
