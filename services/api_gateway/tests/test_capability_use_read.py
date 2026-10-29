# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Who reads the capability-use record (ADR-0042 dec 6): a superuser any tenant's, a tenant's
administrator only their own, whatever they ask for."""
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from routers import capability_use

OWN, OTHER = uuid.uuid4(), uuid.uuid4()


class Db:
    def __init__(self):
        self.params = None

    async def execute(self, statement, params):
        assert "public.capability_use_events" in str(statement)
        self.params = params
        return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))


async def _read(user, **kw):
    db = Db()
    args = dict(company_id=None, handle_id=None, since=None, before_id=None, limit=100)
    args.update(kw)
    await capability_use.list_capability_use(user=user, db=db, **args)
    scope = db.params["company_id"]
    return str(scope) if scope is not None else None


@pytest.mark.asyncio
async def test_an_administrator_reads_only_their_own_tenant():
    admin = SimpleNamespace(is_superuser=False, company_id=OWN)
    assert await _read(admin) == str(OWN)
    assert await _read(admin, company_id=OTHER) == str(OWN)


@pytest.mark.asyncio
async def test_a_superuser_reads_any_tenant_or_all():
    root = SimpleNamespace(is_superuser=True, company_id=None)
    assert await _read(root) is None
    assert await _read(root, company_id=OTHER) == str(OTHER)


@pytest.mark.asyncio
async def test_an_administrator_without_a_tenant_reads_nothing():
    with pytest.raises(HTTPException) as e:
        await _read(SimpleNamespace(is_superuser=False, company_id=None))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_since_is_bound_as_a_time_not_its_text():
    from datetime import datetime, timezone
    db = Db()
    when = datetime(2026, 10, 29, tzinfo=timezone.utc)
    await capability_use.list_capability_use(user=SimpleNamespace(is_superuser=True, company_id=None), db=db,
                                             company_id=None, handle_id=None, since=when, before_id=None,
                                             limit=10)
    assert db.params["since"] is when


def test_only_an_administrator_may_call_it():
    route = next(r for r in capability_use.router.routes if r.path == "/api/capability-use")
    names = [getattr(d.call, "__qualname__", "") for d in route.dependant.dependencies]
    assert any("require_role" in n for n in names)
