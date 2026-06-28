# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the SQL proxy binding policy (ADR-0015 dec 5-6)."""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import binding as b  # noqa: E402


class FakeAsyncStore:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    async def get(self, key):
        return self.mapping.get(key)


def _rec(company_id, audience="sql", user="u", read_only=True):
    return json.dumps({"user": user, "company_id": company_id, "audience": audience, "read_only": read_only})


@pytest.mark.asyncio
async def test_resolve_sql_handle():
    cid = str(uuid.uuid4())
    store = FakeAsyncStore({f"{b._KEY_PREFIX}h": _rec(cid)})
    binding = await b.resolve_sql_binding(store.get, "h")
    assert binding is not None and binding.company_id == cid and binding.read_only


@pytest.mark.asyncio
async def test_api_audience_handle_denied_on_sql_proxy():
    store = FakeAsyncStore({f"{b._KEY_PREFIX}h": _rec(str(uuid.uuid4()), audience="api")})
    assert await b.resolve_sql_binding(store.get, "h") is None


@pytest.mark.asyncio
async def test_absent_empty_malformed_denied():
    store = FakeAsyncStore({f"{b._KEY_PREFIX}bad": "not-json"})
    assert await b.resolve_sql_binding(store.get, "missing") is None
    assert await b.resolve_sql_binding(store.get, "") is None
    assert await b.resolve_sql_binding(store.get, "bad") is None


def test_reader_role_from_uuid():
    cid = "550e8400-e29b-41d4-a716-446655440000"
    assert b.reader_role(cid) == "tenant_reader_550e8400_e29b_41d4_a716_446655440000"


def test_reader_role_rejects_non_uuid():
    with pytest.raises(ValueError):
        b.reader_role("not-a-uuid")


def test_setup_statements_set_role_search_path_then_read_only():
    cid = str(uuid.uuid4())
    stmts = b.session_setup_statements(b.SqlBinding(user="u", company_id=cid, read_only=True))
    # SET ROLE first, then point search_path at the tenant schema, then the read-only default.
    assert stmts[0] == f'SET ROLE "{b.reader_role(cid)}"'
    assert stmts[1] == f'SET search_path TO "{b.tenant_schema(cid)}"'
    assert any("read_only" in s for s in stmts)


def test_tenant_schema_matches_reader_role_uuid():
    cid = str(uuid.uuid4())
    # Both derive from the same validated UUID: tenant_<uuid> and tenant_reader_<uuid>.
    assert b.reader_role(cid) == "tenant_reader_" + b.tenant_schema(cid)[len("tenant_"):]


def test_setup_statements_without_read_only():
    cid = str(uuid.uuid4())
    stmts = b.session_setup_statements(b.SqlBinding(user="u", company_id=cid, read_only=False))
    assert stmts == [f'SET ROLE "{b.reader_role(cid)}"', f'SET search_path TO "{b.tenant_schema(cid)}"']
