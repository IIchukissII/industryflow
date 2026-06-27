# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unified tenant resolution tests (ADR-0015): the data API accepts a platform session OR a
notebook API-capability, and a capability-sourced session runs read-only. Session is mocked
and the capability store is monkeypatched, so no DB or Redis is needed.
"""
import json
import uuid
from types import SimpleNamespace

import pytest


def _request(headers=None):
    return SimpleNamespace(headers=headers or {})


async def _drive_db(dep, ctx):
    agen = dep(ctx=ctx)
    session = await agen.__anext__()
    await agen.aclose()
    return session


@pytest.mark.asyncio
async def test_session_user_resolves_to_full_access():
    import dependencies

    cid = uuid.uuid4()
    user = SimpleNamespace(id=uuid.uuid4(), company_id=cid)
    ctx = await dependencies.resolve_tenant_context(request=_request(), user=user)
    assert ctx.source == "session"
    assert ctx.company_id == str(cid)
    assert ctx.read_only is False


@pytest.mark.asyncio
async def test_capability_resolves_read_only(monkeypatch):
    import dependencies

    cid = str(uuid.uuid4())
    record = json.dumps({"user": "alice", "company_id": cid, "audience": "api", "read_only": True})

    async def fake_get(key):
        return record if key.endswith("good-handle") else None

    monkeypatch.setattr(dependencies, "_capability_get", fake_get, raising=True)

    ctx = await dependencies.resolve_tenant_context(
        request=_request({dependencies.CAPABILITY_HEADER: "good-handle"}), user=None
    )
    assert ctx.source == "capability"
    assert ctx.company_id == cid
    assert ctx.user == "alice"
    assert ctx.read_only is True


@pytest.mark.asyncio
async def test_no_session_no_capability_is_401(monkeypatch):
    import dependencies
    from fastapi import HTTPException

    async def fake_get(key):
        return None

    monkeypatch.setattr(dependencies, "_capability_get", fake_get, raising=True)

    with pytest.raises(HTTPException) as ei:
        await dependencies.resolve_tenant_context(request=_request(), user=None)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_capability_db_is_read_only(fake_session):
    import dependencies

    ctx = dependencies.TenantContext(company_id=str(uuid.uuid4()), user="alice", read_only=True, source="capability")
    await _drive_db(dependencies.get_tenant_db, ctx)

    sql = " ".join(fake_session.executed)
    assert "SET LOCAL search_path TO tenant_" in sql
    assert "transaction read only" in sql.lower()


@pytest.mark.asyncio
async def test_session_db_is_not_read_only(fake_session):
    import dependencies

    ctx = dependencies.TenantContext(company_id=str(uuid.uuid4()), user="u", read_only=False, source="session")
    await _drive_db(dependencies.get_tenant_db, ctx)

    sql = " ".join(fake_session.executed)
    assert "SET LOCAL search_path TO tenant_" in sql
    assert "read only" not in sql.lower()
