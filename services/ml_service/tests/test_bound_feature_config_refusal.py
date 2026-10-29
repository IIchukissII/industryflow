# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The database's refusal of a change to a bound feature config reaches the caller as a 409 naming
the models that bind it (ADR-0041 dec 3), not as a 500.

The repository raises the asyncpg errors the rule raises (init script 30): restrict_violation on an
edit, foreign_key_violation on a delete. The rule itself is proven against the real database in
infrastructure/timescaledb/tests/test_bound_feature_config.py.
"""
import os
import sys

import asyncpg
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import auth  # noqa: E402
from routers import feature_configs  # noqa: E402

CID = "00000000-0000-4000-8000-0000000000c1"
CONFIG_ID = "00000000-0000-4000-8000-000000000002"
BOUND = [{"model_id": "00000000-0000-4000-8000-000000000001", "model_name": "pump-iforest"}]


class Repo:
    def __init__(self, error=None, bound=BOUND):
        self.error, self.bound = error, bound

    async def update_feature_config(self, **_):
        raise self.error

    async def delete_feature_config(self, **_):
        raise self.error

    async def models_bound_to_feature_config(self, company_id, config_id):
        assert (company_id, config_id) == (CID, CONFIG_ID)
        return self.bound


def _client(repo):
    app = FastAPI()
    app.include_router(feature_configs.router)
    app.state.ml_repository = repo
    app.dependency_overrides[feature_configs.get_company_id_dependency] = lambda: CID
    app.dependency_overrides[auth.require_writer] = lambda: None
    return TestClient(app)


@pytest.mark.parametrize("method,error", [
    ("put", asyncpg.RestrictViolationError("bound")),
    ("delete", asyncpg.ForeignKeyViolationError("bound")),
])
def test_a_refused_change_is_a_409_naming_the_binding_models(method, error):
    client = _client(Repo(error))
    kwargs = {"json": {"base_sensors": ["temp_1", "flow_1"]}} if method == "put" else {}
    r = getattr(client, method)(f"/api/feature-configs/{CONFIG_ID}", **kwargs)
    assert r.status_code == 409
    assert "pump-iforest" in r.json()["detail"] and BOUND[0]["model_id"] in r.json()["detail"]


def test_another_database_failure_is_not_reported_as_bound():
    r = _client(Repo(asyncpg.UniqueViolationError("dup"))).put(
        f"/api/feature-configs/{CONFIG_ID}", json={"name": "x"})
    assert r.status_code == 500
