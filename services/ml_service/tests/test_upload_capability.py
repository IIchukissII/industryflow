# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for demand-minting the upload capability (ADR-0030 dec 3, ADR-0015 dec 4). Pure
rule, no store — mirroring the other capability-rule tests.

The property under test is not "a handle comes back". It is that the conditions ADR-0015 dec 4
attaches to demand-minting are kept by construction: the tenant is the caller's verified one and is
never invented, the handle is opaque and short-lived, and the audience is one the spawner cannot
mint — because the audience is what holds uploads and kernels to different admission rules.
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import upload_capability as uc  # noqa: E402
from industryflow_contracts import capability_audit as audit  # noqa: E402

CID = str(uuid.uuid4())


class FakeStore:
    """``mint``'s adapter: the entry and its event arrive in one call, as one transaction would."""

    def __init__(self):
        self.written = {}
        self.events = []

    def set(self, key, value, ttl_seconds, event):
        self.written[key] = (value, ttl_seconds)
        self.events.append(dict(event))


def _minted(store):
    (key, (raw, ttl)), = store.written.items()
    return key, json.loads(raw), ttl


# --- what a minted capability is bound to -----------------------------------------------------

def test_mint_binds_the_verified_user_and_exactly_one_tenant():
    s = FakeStore()
    handle = uc.mint(s.set, user="alice@example.com", company_id=CID)
    key, rec, _ = _minted(s)
    assert key == f"nbcap:{handle}"
    assert rec["user"] == "alice@example.com"
    assert rec["company_id"] == CID
    assert rec["audience"] == uc.AUDIENCE_UPLOAD


def test_the_upload_plane_is_not_read_only():
    # ADR-0015 dec 1: a bound fact per handle. This plane writes one artifact; its boundary is
    # the tenant namespace the mediator enforces, not read-only-ness.
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID)
    _, rec, _ = _minted(s)
    assert rec["read_only"] is False


def test_the_audience_is_upload_and_nothing_else():
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID)
    _, rec, _ = _minted(s)
    assert rec["audience"] == "upload"
    assert rec["audience"] not in ("tracking", "api", "sql", "cold")


# --- the tenant is never invented (ADR-0003 dec 1) ---------------------------------------------

def test_mint_refuses_without_a_tenant():
    # A handle bound to nothing is not a weaker capability; it is a hole.
    s = FakeStore()
    with pytest.raises(ValueError):
        uc.mint(s.set, user="alice", company_id="")
    assert s.written == {}       # nothing was minted


def test_mint_refuses_without_a_verified_user():
    s = FakeStore()
    with pytest.raises(ValueError):
        uc.mint(s.set, user="", company_id=CID)
    assert s.written == {}


def test_mint_refuses_a_tenant_that_is_not_a_uuid():
    # The value becomes a namespace; a namespace that can be anything is not a boundary (ADR-0003).
    s = FakeStore()
    for bad in ("'; DROP", "tenant_x", "../../etc", "0b2f503a"):
        with pytest.raises(ValueError):
            uc.mint(s.set, user="alice", company_id=bad)
    assert s.written == {}


def test_the_tenant_is_canonicalised_not_echoed():
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID.upper())
    _, rec, _ = _minted(s)
    assert rec["company_id"] == CID          # canonical lowercase form, one spelling per tenant


# --- shape: opaque, high-entropy, short-lived --------------------------------------------------

def test_handles_are_opaque_and_never_repeat():
    s = FakeStore()
    handles = {uc.mint(s.set, user="alice", company_id=CID) for _ in range(200)}
    assert len(handles) == 200
    for h in handles:
        assert len(h) >= 40                  # 32 bytes, urlsafe-encoded
        assert CID not in h                  # the handle carries no facts — its authority is the entry


def test_it_expires_and_the_ttl_is_one_upload_not_one_session():
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID)
    _, _, ttl = _minted(s)
    assert ttl == uc.DEFAULT_TTL_SECONDS
    assert 0 < ttl <= 900


def test_mint_refuses_a_capability_that_would_not_expire():
    s = FakeStore()
    for bad_ttl in (0, -1):
        with pytest.raises(ValueError):
            uc.mint(s.set, user="alice", company_id=CID, ttl_seconds=bad_ttl)
    assert s.written == {}


# --- no session secret travels with it ---------------------------------------------------------

def test_the_record_holds_bound_facts_and_nothing_resembling_a_secret():
    # ADR-0015 dec 7: a reader of the store sees revocable, single-tenant handles and nothing whose
    # loss is worse than that.
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID)
    _, rec, _ = _minted(s)
    assert set(rec) == {"user", "company_id", "audience", "read_only", "handle_id"}


# --- the mint is recorded with it (ADR-0042) ---------------------------------------------------

def test_a_mint_binds_a_handle_id_and_writes_its_minted_event_with_it():
    s = FakeStore()
    handle = uc.mint(s.set, user="alice", company_id=CID.upper())
    _, rec, _ = _minted(s)
    assert str(uuid.UUID(rec["handle_id"])) == rec["handle_id"]
    (event,) = [audit.decode(e) for e in s.events]
    assert {k: event[k] for k in ("kind", "plane", "user", "company_id", "audience", "handle_id")} == {
        "kind": "minted", "plane": "ml_service", "user": "alice", "company_id": CID,
        "audience": "upload", "handle_id": rec["handle_id"]}
    assert handle not in json.dumps(s.events)


def test_every_mint_gets_its_own_handle_id():
    s = FakeStore()
    for _ in range(20):
        uc.mint(s.set, user="alice", company_id=CID)
    assert len({e["handle_id"] for e in s.events}) == 20


def test_a_refused_mint_records_nothing():
    s = FakeStore()
    with pytest.raises(ValueError):
        uc.mint(s.set, user="alice", company_id="tenant_x")
    assert s.events == []


# --- a suspended company is issued nothing (ADR-0033 dec 3) ------------------------------------

def _mint_route(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import auth
    from routers import models

    app = FastAPI()
    app.include_router(models.router)
    app.state.capability_store = store
    app.dependency_overrides[auth.verify_jwt_token] = lambda: {"payload": {"sub": "user-1"}, "role": "engineer"}
    app.dependency_overrides[models.get_company_id_dependency] = lambda: CID
    return TestClient(app).post("/api/models/upload-capability")


class Store:
    """The route's store: an async client on a real Redis protocol (fakeredis), read back through a
    sync client on the same server. ``transactions`` is every pipeline the route executed:
    (was it MULTI/EXEC, the commands it carried)."""

    def __init__(self, mapping=None):
        import fakeredis

        server = fakeredis.FakeServer()
        self.sync = fakeredis.FakeRedis(server=server, decode_responses=True)
        for key, value in (mapping or {}).items():
            self.sync.set(key, value)
        self.client = fakeredis.aioredis.FakeRedis(server=server)
        self.transactions = []
        real = self.client.pipeline

        def pipeline(transaction=True, shard_hint=None):
            pipe = real(transaction=transaction, shard_hint=shard_hint)
            execute = pipe.execute

            async def recorded(*a, **k):
                self.transactions.append((transaction, [str(c[0][0]).upper() for c in pipe.command_stack]))
                return await execute(*a, **k)

            pipe.execute = recorded
            return pipe

        self.client.pipeline = pipeline

    def events(self):
        return [audit.decode(f) for _i, f in self.sync.xrange(audit.STREAM_KEY)]


def test_the_route_mints_for_an_active_tenant():
    store = Store()
    r = _mint_route(store.client)
    assert r.status_code == 201, r.text
    assert store.sync.get(f"nbcap:{r.json()['capability']}") is not None


def test_the_route_indexes_the_handle_under_its_user():
    # Ending the user's sessions revokes it with their other capabilities (ADR-0004 dec 2).
    store = Store()
    r = _mint_route(store.client)
    assert store.sync.smembers(uc.user_index_key("user-1")) == {r.json()["capability"]}
    assert 0 < store.sync.ttl(uc.user_index_key("user-1")) <= uc.DEFAULT_TTL_SECONDS
    assert uc.user_index_key("user-1") == "nbcap-upload-user:user-1"


def test_the_route_writes_the_mint_and_its_record_in_one_transaction():
    # ADR-0042 dec 3: the index, the entry and the minted event together or not at all.
    store = Store()
    r = _mint_route(store.client)
    handle = r.json()["capability"]
    assert store.transactions == [(True, ["SADD", "EXPIRE", "SET", "XADD"])]
    entry = json.loads(store.sync.get(f"nbcap:{handle}"))
    (event,) = store.events()
    assert (event["kind"], event["plane"], event["audience"], event["user"], event["company_id"]) == (
        "minted", "ml_service", "upload", "user-1", CID)
    assert event["handle_id"] == entry["handle_id"]
    assert handle not in json.dumps(store.sync.xrange(audit.STREAM_KEY))


def test_the_route_refuses_a_suspended_tenant():
    store = Store({uc.suspended_key(CID): "1"})
    r = _mint_route(store.client)
    assert r.status_code == 403
    assert not [k for k in store.sync.keys() if k.startswith("nbcap:")]
    assert store.events() == [] and store.transactions == []
