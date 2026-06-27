# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the SSO handoff endpoint (ADR-0014).

The handler is called directly with a user (the auth/tenant dependency is exercised elsewhere),
so no app startup, database, or redis is needed.
"""
import uuid
from types import SimpleNamespace

import pytest


@pytest.mark.asyncio
async def test_verify_returns_identity_headers():
    from routers import auth_verify

    uid, cid = uuid.uuid4(), uuid.uuid4()
    user = SimpleNamespace(id=uid, company_id=cid, role="engineer")

    resp = await auth_verify.verify_session(user=user)

    # 2xx so the SSO proxy's auth_request allows the request.
    assert resp.status_code == 204
    assert resp.headers[auth_verify.HEADER_USER] == str(uid)
    assert resp.headers[auth_verify.HEADER_COMPANY_ID] == str(cid)
    assert resp.headers[auth_verify.HEADER_ROLE] == "engineer"


@pytest.mark.asyncio
async def test_verify_header_names_match_hub_contract():
    # The proxy/hub read these exact names (services/notebook_hub/identity.py).
    from routers import auth_verify

    assert auth_verify.HEADER_USER == "X-IF-User"
    assert auth_verify.HEADER_COMPANY_ID == "X-IF-Company-Id"
    assert auth_verify.HEADER_ROLE == "X-IF-Role"


@pytest.mark.asyncio
async def test_verify_tolerates_missing_role():
    from routers import auth_verify

    user = SimpleNamespace(id=uuid.uuid4(), company_id=uuid.uuid4(), role=None)
    resp = await auth_verify.verify_session(user=user)
    assert resp.headers[auth_verify.HEADER_ROLE] == ""
