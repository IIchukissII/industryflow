# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tenant-scoping choke-point tests (ADR-0003).

Every data endpoint in the gateway (measurements, aggregations, training-data) reads tenant
data through get_db_with_tenant, which scopes the connection to the caller's tenant schema
with SET LOCAL search_path. These tests pin that single choke point: the correct schema is
selected for a user's company_id, and a non-UUID company_id is rejected before it can reach
SQL (the injection defence).

Full HTTP-level multi-tenant integration (two users, two tenants, over the wire) is left to a
later integration-CI expansion; here the database session is mocked so the scoping logic is
tested in isolation.
"""
import uuid

import pytest


def test_normalize_company_id_to_schema_valid():
    import dependencies

    cid = "550e8400-e29b-41d4-a716-446655440000"
    assert (
        dependencies.normalize_company_id_to_schema(cid)
        == "tenant_550e8400_e29b_41d4_a716_446655440000"
    )


@pytest.mark.parametrize("bad", ["", "abc", "not-a-uuid", "550e8400; DROP SCHEMA public"])
def test_normalize_company_id_rejects_non_uuid(bad):
    import dependencies

    # A non-UUID raises before the value can be interpolated into a search_path statement.
    with pytest.raises(ValueError):
        dependencies.normalize_company_id_to_schema(bad)


class _User:
    def __init__(self, company_id):
        self.company_id = company_id


async def _drive(dep, user):
    """Run the get_db_with_tenant async generator to first yield, then close it."""
    agen = dep(current_user=user)
    session = await agen.__anext__()
    await agen.aclose()
    return session


@pytest.mark.asyncio
async def test_get_db_with_tenant_sets_search_path(fake_session):
    import dependencies

    cid = uuid.uuid4()
    await _drive(dependencies.get_db_with_tenant, _User(cid))

    schema = f"tenant_{str(cid).replace('-', '_')}"
    assert any(
        f"SET LOCAL search_path TO {schema}, public" in sql for sql in fake_session.executed
    ), fake_session.executed


@pytest.mark.asyncio
async def test_get_db_with_tenant_skips_when_no_company(fake_session):
    import dependencies

    await _drive(dependencies.get_db_with_tenant, _User(None))

    # No company_id → no search_path is set (the dependency logs a warning instead).
    assert not any("search_path" in sql for sql in fake_session.executed), fake_session.executed
