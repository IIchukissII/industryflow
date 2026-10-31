# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The end-to-end suite (ADR-0026 dec 8, 10): journeys through a deployed stack's public edges, on the
long-lived environment, with the internals reached only where a journey needs what no edge offers
(a suspension older than the deletion floor, the deletion job, the device CA).

Run by scripts/box-e2e.sh, which builds tests/e2e/Dockerfile, creates the run's platform superuser and
removes it afterwards. Without ``IF_E2E_EDGE`` every journey skips; with ``IF_E2E_REQUIRE=1`` a skip
is a failure, so a run that validates a commit cannot pass by skipping.

| Variable | Meaning |
|---|---|
| ``IF_E2E_EDGE`` | The HTTPS edge, ``https://<host>`` |
| ``IF_E2E_INGEST`` | The device edge, ``https://<host>:8443/ingest`` |
| ``IF_E2E_CA`` | The edges' CA certificate |
| ``IF_E2E_SUPERUSER_EMAIL``, ``IF_E2E_SUPERUSER_PASSWORD`` | The run's platform superuser |
| ``IF_E2E_COMPOSE_DIR`` | The deployment's compose directory, at the same path as on the host |
| ``IF_E2E_WORK`` | A scratch directory for the run, at the same path as on the host |
| ``IF_E2E_AUTHORING_IMAGE`` | The deployment's notebook authoring image, where a model is built |
"""
from __future__ import annotations

import os
import uuid

import pytest

from harness import Edge, Environment, Secret

REQUIRED = ("IF_E2E_EDGE", "IF_E2E_INGEST", "IF_E2E_CA", "IF_E2E_SUPERUSER_EMAIL",
            "IF_E2E_SUPERUSER_PASSWORD", "IF_E2E_COMPOSE_DIR", "IF_E2E_WORK", "IF_E2E_AUTHORING_IMAGE")
# Read only where it is used, never part of what a fixture hands a test.
SECRET = "IF_E2E_SUPERUSER_PASSWORD"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """With IF_E2E_REQUIRE=1 a skipped journey fails the run: a validation cannot pass by skipping."""
    outcome = yield
    report = outcome.get_result()
    if os.environ.get("IF_E2E_REQUIRE") == "1" and report.skipped and call.when in ("setup", "call"):
        report.outcome = "failed"
        report.longrepr = f"skipped under IF_E2E_REQUIRE=1: {report.longrepr}"


@pytest.fixture(scope="session")
def settings():
    missing = [name for name in REQUIRED if not os.environ.get(name)]
    if missing:
        pytest.skip(f"no deployed stack to run against: {', '.join(missing)} unset")
    return {name: os.environ[name] for name in REQUIRED if name != SECRET}


@pytest.fixture(scope="session")
def edge(settings):
    with Edge(settings["IF_E2E_EDGE"], settings["IF_E2E_CA"]) as e:
        yield e


@pytest.fixture(scope="session")
def env(settings):
    return Environment(settings["IF_E2E_COMPOSE_DIR"])


@pytest.fixture(scope="session")
def root(edge, settings):
    """The run's platform superuser, signed in."""
    return edge.session(settings["IF_E2E_SUPERUSER_EMAIL"], Secret(os.environ[SECRET]))


@pytest.fixture(scope="session")
def tenant(edge, root, env):
    """A tenant of this run, with an administrator and an engineer signed in. At the end it is suspended,
    its deletion requested and then its devices revoked; the next run's deletion job removes it
    (test_tenant_life.py). The job accepts only a CRL signed after the request (ADR-0033), so the
    revocation comes last: one made before the request leaves the tenant pending until the CRL is
    next re-signed."""
    run = uuid.uuid4().hex[:8]
    company = root.post("/api/companies", json={"company_name": f"e2e-{run}"}).json()
    cid = company["company_id"]
    password = Secret(f"E2e-{uuid.uuid4().hex}")
    users = {}
    for role in ("admin", "engineer"):
        email = f"e2e-{run}-{role}@example.com"
        root.post("/api/users", json={"email": email, "password": password.reveal(), "role": role,
                                      "company_id": cid})
        users[role] = edge.session(email, password)
    yield {"company_id": cid, "schema": company.get("schema_name"), "run": run, **users}
    root.put(f"/api/companies/{cid}", json={"is_active": False}, expect=(200, 400, 404))
    env.sql(f"UPDATE public.companies SET suspended_at = now() - interval '31 days' WHERE company_id = '{cid}'")
    root.post(f"/api/companies/{cid}/deletion", expect=(200, 201, 202, 409))
    env.script("scripts/device-ca.sh", "revoke-tenant", cid)


@pytest.fixture(scope="session")
def device(env, tenant, settings):
    """A certificate for a device of the run's tenant, as the operator enrols one (scripts/device-ca.sh)."""
    device_id = f"e2e-dev-{tenant['run']}"
    env.script("scripts/device-ca.sh", "issue", tenant["company_id"], device_id)
    base = os.path.join(settings["IF_E2E_COMPOSE_DIR"], "deploy", "device-ca", "devices", tenant["company_id"],
                        device_id)
    return os.path.join(base, "current.chain.crt"), os.path.join(base, "current.key")
