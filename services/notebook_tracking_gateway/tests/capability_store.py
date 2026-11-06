# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability store the gateway's tests run on: industryflow_contracts.testing's (a real Redis in
CI), connected as gateway.py connects (bytes back), one client per event loop since TestClient runs
the app on its own; and its capability-use record (ADR-0042) read back from the stream."""
import asyncio

from industryflow_contracts import testing

# Stores whose entries the test must find still there when it ends (conftest.py).
NEVER_CONSUMED = []


def capabilities(entries=None, *, consumable=True):
    """A store holding ``entries``. With ``consumable=False`` a removal of any of them fails the test:
    the tracking plane never consumes a handle."""
    store = testing.AnyLoopRedis(entries)
    if not consumable:
        NEVER_CONSUMED.append((store, sorted(entries or {})))
    return store


def events(store, kind=None):
    return asyncio.run(testing.audit_events(store, kind))


def stream(store):
    return asyncio.run(testing.audit_stream(store))
