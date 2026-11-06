# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability store the gateway's tests run on: industryflow_contracts.testing's (a real Redis in
CI), connected as gateway.py connects (bytes back), one client per event loop since TestClient runs
the app on its own; and its capability-use record (ADR-0042) read back from the stream."""
import asyncio

from industryflow_contracts import capability_audit as audit
from industryflow_contracts import testing

# Stores this plane must not remove anything from, checked when the test ends (conftest.py):
# ``(store, keys it held when made, every removal asked of it)``.
NEVER_CONSUMED = []
REMOVALS = ("delete", "unlink", "getdel", "getex")


def capabilities(entries=None, *, consumable=True):
    """A store holding ``entries``. With ``consumable=False`` any removal asked of it fails the test,
    whether or not the key was there: the tracking plane never consumes a handle."""
    store = testing.AnyLoopRedis(entries)
    if not consumable:
        asked = []

        def watched(command):
            async def run(*args, **kwargs):
                if command != "eval" or args[:1] == (audit.DELETE_RECORDED_SCRIPT,):
                    asked.append((command, args))
                return await getattr(store.loop_client(), command)(*args, **kwargs)
            return run

        for command in (*REMOVALS, "eval"):
            setattr(store, command, watched(command))
        NEVER_CONSUMED.append((store, keyspace(store), asked))
    return store


def keyspace(store):
    """The store's keys, not counting the capability-use record's own (its stream and markers)."""
    with store.sync() as client:
        return {k.decode() for k in client.keys("*") if not k.startswith(b"nbcap-audit")}


def events(store, kind=None):
    return asyncio.run(testing.audit_events(store, kind))


def stream(store):
    return asyncio.run(testing.audit_stream(store))
