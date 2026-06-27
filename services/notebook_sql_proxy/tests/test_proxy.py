# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the SQL proxy connection orchestration (ADR-0015), with a fake backend/store."""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import binding as b  # noqa: E402
import proxy  # noqa: E402


class FakeAsyncStore:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    async def get(self, key):
        return self.mapping.get(key)


class FakeBackend:
    def __init__(self):
        self.connected = False
        self.executed: list[str] = []
        self.relayed = False
        self.closed = False

    async def connect_privileged(self):
        self.connected = True

    async def execute(self, sql):
        self.executed.append(sql)

    async def relay(self):
        self.relayed = True

    async def close(self):
        self.closed = True


def _sql_record(company_id):
    return json.dumps({"user": "alice", "company_id": company_id, "audience": "sql", "read_only": True})


@pytest.mark.asyncio
async def test_valid_handle_binds_tenant_and_relays():
    cid = str(uuid.uuid4())
    store = FakeAsyncStore({f"{b._KEY_PREFIX}good": _sql_record(cid)})
    backends = []

    def factory(binding):
        be = FakeBackend()
        backends.append(be)
        return be

    ok = await proxy.serve_connection("good", store.get, factory)

    assert ok is True
    assert len(backends) == 1
    be = backends[0]
    assert be.connected and be.relayed and be.closed
    # The connection assumed the tenant's read-only role before relaying.
    assert be.executed[0] == f'SET ROLE "{b.reader_role(cid)}"'
    assert any("read_only" in s for s in be.executed)


@pytest.mark.asyncio
async def test_invalid_handle_opens_no_backend():
    store = FakeAsyncStore({})  # nothing minted
    opened = []

    def factory(binding):
        opened.append(binding)
        return FakeBackend()

    ok = await proxy.serve_connection("nope", store.get, factory)

    assert ok is False
    # The privileged principal is never used for an unresolved handle.
    assert opened == []


@pytest.mark.asyncio
async def test_api_handle_is_rejected_by_sql_proxy():
    cid = str(uuid.uuid4())
    api_rec = json.dumps({"user": "a", "company_id": cid, "audience": "api", "read_only": True})
    store = FakeAsyncStore({f"{b._KEY_PREFIX}apionly": api_rec})

    ok = await proxy.serve_connection("apionly", store.get, lambda binding: FakeBackend())
    assert ok is False
