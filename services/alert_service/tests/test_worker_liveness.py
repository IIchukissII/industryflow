# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The worker process must not outlive its loops, and its health check must see a dead loop.

Before: main gathered the loops with return_exceptions=True, so a consumer loop that ended left the
other loops running and the process up; the health check only asked whether PID 1 was python.
"""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "worker"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import healthcheck  # noqa: E402
from main import wait_for_first_exit  # noqa: E402


async def _forever():
    await asyncio.Event().wait()


async def _ends():
    return None


async def _raises():
    raise RuntimeError("consumer died")


def _supervise(*coros, running=True):
    async def go():
        tasks = {asyncio.create_task(c): f"loop{i}" for i, c in enumerate(coros)}
        # Bounded: a supervisor that waits on the surviving loops would otherwise hang forever.
        await asyncio.wait_for(wait_for_first_exit(tasks, lambda: running), timeout=5)
        return tasks
    return asyncio.run(go())


def test_a_loop_that_raises_fails_the_service():
    with pytest.raises(RuntimeError, match="loop1 ended unexpectedly.*consumer died"):
        _supervise(_forever(), _raises())


def test_a_loop_that_returns_while_running_fails_the_service():
    with pytest.raises(RuntimeError, match="loop0 ended unexpectedly"):
        _supervise(_ends(), _forever())


def test_the_surviving_loops_are_cancelled():
    async def go():
        survivor = asyncio.create_task(_forever())
        tasks = {survivor: "survivor", asyncio.create_task(_raises()): "dead"}
        with pytest.raises(RuntimeError):
            await asyncio.wait_for(wait_for_first_exit(tasks, lambda: True), timeout=5)
        return survivor
    assert asyncio.run(go()).cancelled()


def test_loops_ending_on_shutdown_are_not_a_failure():
    _supervise(_ends(), _forever(), running=False)


def test_health_check_fails_on_a_stale_or_missing_heartbeat(tmp_path, monkeypatch):
    hb = tmp_path / "hb"
    monkeypatch.setattr(healthcheck.config, "HEARTBEAT_FILE", str(hb))
    monkeypatch.setattr(healthcheck.config, "HEARTBEAT_MAX_AGE_SECONDS", 120.0)
    assert healthcheck.main() == 1          # never touched: the loop never started

    hb.touch()
    assert healthcheck.main() == 0

    old = time.time() - 300
    os.utime(hb, (old, old))
    assert healthcheck.main() == 1          # the loop stopped polling
