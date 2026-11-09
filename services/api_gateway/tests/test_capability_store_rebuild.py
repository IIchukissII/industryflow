# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability store's rebuild (ADR-0015 dec 8): denials re-derived from the registry, then the record.

The store is industryflow_contracts.testing's (a real Redis in CI); the registry is a stand-in that
answers the two statements the rebuild runs, so a pass can see a company reactivated between them.
"""
import asyncio
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

import capability_store_rebuild as rebuild
from industryflow_contracts import denials, sessions, testing

SUSPENDED, ACTIVE, FLIPS = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
                            "33333333-3333-4333-8333-333333333333")


class Registry:
    """``inactive`` answers the first statement; ``active_on_reread`` the re-read of the marked."""

    def __init__(self, inactive, active_on_reread=()):
        self.inactive, self.active_on_reread, self.reads = list(inactive), set(active_on_reread), []

    @asynccontextmanager
    async def begin(self):
        yield self

    async def execute(self, statement, params):
        self.reads.append(statement)
        if statement is rebuild.INACTIVE_COMPANIES:
            ids = self.inactive
        else:
            ids = [i for i in params["ids"] if i in self.active_on_reread]
        return SimpleNamespace(fetchall=lambda: [SimpleNamespace(company_id=i) for i in ids])


def _on_a_store(test):
    """Run ``test(store)`` on a store recreated empty (not rebuilt), opened in the test's own loop."""
    async def run():
        store = await testing.async_redis(rebuilt=False, decode_responses=True)
        try:
            return await test(store)
        finally:
            await store.aclose()

    return asyncio.run(run())


def test_a_store_recreated_empty_gets_every_marker_then_its_record():
    async def run(store):
        assert await denials.tenant_refusal(store.get, ACTIVE) == denials.STORE_NOT_REBUILT
        before = time.time()
        assert await rebuild.rebuild_once(store, Registry([SUSPENDED])) is True
        assert await denials.tenant_refusal(store.get, SUSPENDED) == denials.TENANT_SUSPENDED
        assert await denials.tenant_refusal(store.get, ACTIVE) is None
        # The record's time ends every session issued before it: those records were lost with the store.
        rebuilt = float(await store.get(denials.STORE_REBUILT_KEY))
        assert before <= rebuilt <= time.time()
        assert await sessions.session_ended(store, "u", "s", before - 60) is True
        assert await sessions.session_ended(store, "u", "s", rebuilt + sessions.CLOCK_SKEW_SECONDS + 1) is False

    _on_a_store(run)


def test_a_rebuilt_store_is_not_rebuilt_again_but_a_lost_marker_comes_back():
    async def run(store):
        await rebuild.rebuild_once(store, Registry([]))
        first = await store.get(denials.STORE_REBUILT_KEY)
        # A later pass on a store that kept its record ends no session, and restores a missing marker.
        assert await rebuild.rebuild_once(store, Registry([SUSPENDED])) is False
        assert await store.get(denials.STORE_REBUILT_KEY) == first
        assert await store.get(denials.suspended_key(SUSPENDED)) == "1"

    _on_a_store(run)


def test_a_company_reactivated_during_the_pass_keeps_no_marker():
    """Reactivation removes its marker after its commit; a marker written from an earlier read would
    otherwise outlive it and refuse an active company for ever."""
    async def run(store):
        await rebuild.rebuild_once(store, Registry([SUSPENDED, FLIPS], active_on_reread={FLIPS}))
        assert await store.get(denials.suspended_key(SUSPENDED)) == "1"
        assert await store.get(denials.suspended_key(FLIPS)) is None

    _on_a_store(run)


def test_markers_are_only_added():
    """A marker the registry does not list is left: a suspension writes its marker after its commit,
    so an earlier read must not undo it."""
    async def run(store):
        await store.set(denials.suspended_key(ACTIVE), "1")
        await rebuild.rebuild_once(store, Registry([]))
        assert await store.get(denials.suspended_key(ACTIVE)) == "1"

    _on_a_store(run)


def test_a_registry_with_nothing_to_mark_is_not_read_again():
    registry = Registry([])
    _on_a_store(lambda store: rebuild.rebuild_once(store, registry))
    assert registry.reads == [rebuild.INACTIVE_COMPANIES]


def test_a_failed_pass_is_retried(monkeypatch):
    calls = []

    async def flaky(redis, engine):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("registry down")
        raise asyncio.CancelledError

    monkeypatch.setattr(rebuild, "rebuild_once", flaky)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(rebuild.run(None, None, interval=0))
    assert len(calls) == 2
