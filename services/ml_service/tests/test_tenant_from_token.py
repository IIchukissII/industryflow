# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The caller's tenant is the verified token's company_id claim — never a scan (ADR-0003 dec 1, 2).

Every router here used to fall back, for a token without the claim, to listing every ``tenant_%``
schema and probing each for the user (review finding X2): an O(tenants) walk on the request path
that also made a database lookup, not the credential, the source of the tenant. The gateway signs
the claim into every access token, so there is nothing to fall back to: a token without a valid
claim names no tenant and is refused, and the database is never asked.
"""
import asyncio
import os
import sys
import uuid

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import auth  # noqa: E402
from routers import feature_configs, inference, models  # noqa: E402

CID = "550e8400-e29b-41d4-a716-446655440000"


class _NoDatabase:
    """A pool that fails the test if resolution touches it."""

    def __init__(self):
        self.acquired = 0

    def acquire(self):
        self.acquired += 1
        raise AssertionError("tenant resolution reached the database")


def _client(dependency, payload):
    app = FastAPI()
    app.state.db_pool = _NoDatabase()

    @app.get("/whoami")
    async def whoami(company_id: str = Depends(dependency)):
        return {"company_id": company_id}

    app.dependency_overrides[auth.verify_jwt_token] = lambda: {"user_id": "user-1", "payload": payload}
    return app, TestClient(app, raise_server_exceptions=False)


DEPENDENCIES = [models.get_company_id_dependency, feature_configs.get_company_id_dependency]


@pytest.mark.parametrize("dependency", DEPENDENCIES)
def test_the_tenant_is_the_claim_canonicalized(dependency):
    app, client = _client(dependency, {"company_id": CID.upper()})
    r = client.get("/whoami")
    assert r.status_code == 200 and r.json() == {"company_id": CID}
    assert app.state.db_pool.acquired == 0


@pytest.mark.parametrize("dependency", DEPENDENCIES)
@pytest.mark.parametrize("payload", [{}, {"company_id": None}, {"company_id": ""},
                                     {"company_id": "tenant_x'; DROP SCHEMA public; --"}])
def test_a_token_without_a_valid_claim_is_refused_without_a_scan(dependency, payload):
    app, client = _client(dependency, payload)
    r = client.get("/whoami")
    assert r.status_code == 401, r.text
    assert app.state.db_pool.acquired == 0


def test_inference_resolves_a_user_caller_from_the_claim_alone(monkeypatch):
    """The predict path's user branch: the same claim, and no scan when it is missing."""
    monkeypatch.delenv("INTERNAL_SERVICE_TOKEN", raising=False)

    class _Request:
        headers = {"Authorization": "Bearer x"}
        cookies = {}

    body = inference.InferenceRequest(model_id=str(uuid.uuid4()), sensor_data={}, company_id=str(uuid.uuid4()))

    async def verified(payload):
        async def verify_jwt_token(request=None, authorization=None, if_access=None):
            return {"user_id": "user-1", "payload": payload}
        monkeypatch.setattr(auth, "verify_jwt_token", verify_jwt_token)
        return await inference._resolve_company_id(_Request(), body)

    # The body's company_id is never the tenant for a user caller (ADR-0003 dec 3).
    assert asyncio.run(verified({"company_id": CID})) == CID
    with pytest.raises(HTTPException) as refused:
        asyncio.run(verified({}))
    assert refused.value.status_code == 401
