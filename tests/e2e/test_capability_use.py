# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A capability's use and its record, through the public edge and the deployed writer (ADR-0026
dec 10): a handle is honoured by its own plane only, its first use is recorded once, a kernel that
repeats a refusal leaves one row and a count, and the record reaches the database through the real
store and driver (ADR-0015, ADR-0042).

The handles are placed in the capability store as the hub's spawn mints them (ADR-0015 dec 4); no
edge mints a kernel's handle."""
import json
import secrets
import uuid

import pytest

from harness import eventually, pretty

HEADER = "X-IF-Capability"
LATEST = "/api/measurements/latest"


@pytest.fixture(scope="module")
def handles(env, tenant):
    me = tenant["admin"].get("/users/me").json()
    minted = {}
    for audience in ("api", "sql"):
        handle, hid = secrets.token_urlsafe(32), str(uuid.uuid4())
        entry = {"user": me["id"], "company_id": tenant["company_id"], "audience": audience,
                 "read_only": True, "handle_id": hid}
        env.redis("SET", f"nbcap:{handle}", json.dumps(entry), "EX", "900")
        minted[audience] = (handle, hid)
    yield minted
    for handle, _ in minted.values():
        env.redis("DEL", f"nbcap:{handle}")


def _events(tenant, hid):
    return tenant["admin"].get("/api/capability-use", params={"handle_id": hid}).json()["events"]


def test_a_handle_is_honoured_by_its_own_plane_and_its_first_use_recorded_once(edge, tenant, handles):
    handle, hid = handles["api"]
    for _ in range(3):
        r = edge.anonymous(LATEST, headers={HEADER: handle})
        assert r.status_code == 200, r.text[:300]
    first = eventually(lambda: [e for e in _events(tenant, hid) if e["kind"] == "first_use"],
                       timeout=60, what="the first use reaches the record")
    assert len(first) == 1 and first[0]["plane"] == "api_gateway", pretty(first)
    assert first[0]["company_id"] == tenant["company_id"]


def test_a_kernel_repeating_a_refused_handle_leaves_one_row(edge, tenant, handles, env):
    handle, hid = handles["sql"]
    for _ in range(40):
        assert edge.anonymous(LATEST, headers={HEADER: handle}).status_code == 401
    refused = eventually(lambda: [e for e in _events(tenant, hid) if e["kind"] == "refused"],
                         timeout=60, what="the refusal reaches the record")
    assert len(refused) == 1, pretty(refused)  # once per window, the rest counted (ADR-0042 dec 8)
    assert refused[0]["reason"] == "wrong_audience" and refused[0]["digest"] is None
    eventually(lambda: env.redis("XLEN", "nbcap-audit") == "(integer) 0", timeout=60,
               what="the writer drains the stream")


def test_a_revoked_handle_is_refused_on_its_next_use(edge, handles, env):
    handle, _hid = handles["api"]
    env.redis("DEL", f"nbcap:{handle}")
    assert edge.anonymous(LATEST, headers={HEADER: handle}).status_code == 401


def test_the_superuser_reads_the_record_and_the_tenants_administrator_only_their_own(root, tenant, handles):
    # A tenant administrator's scope is their tenant whatever they ask for (ADR-0042 dec 6).
    _handle, hid = handles["api"]
    assert root.get("/api/capability-use", params={"handle_id": hid}).json()["events"]
    assert all(e["company_id"] == tenant["company_id"] for e in _events(tenant, hid))
    everyone = tenant["admin"].get("/api/capability-use", params={"limit": 1000}).json()["events"]
    assert {e["company_id"] for e in everyone} <= {tenant["company_id"]}
