# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for capability mint/resolve/revoke (ADR-0015) and their record (ADR-0042), through the Redis
adapter on industryflow_contracts.testing's store (a real Redis in CI), connected as
jupyterhub_config.py connects (bytes back). The record's scripts are the contract's own, run by the
store; what each test checks is read back from it.
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import capabilities as cap  # noqa: E402
from industryflow_contracts import capability_audit as audit  # noqa: E402
from industryflow_contracts import denials, testing  # noqa: E402


def _store():
    """``RedisCapabilityStore`` on an empty database, keeping ``(command, key)`` of what each MULTI/EXEC
    its client executes carried."""
    client = testing.sync_redis()
    client.multis = []
    pipeline = client.pipeline

    def counted(transaction=True, shard_hint=None):
        pipe = pipeline(transaction=transaction, shard_hint=shard_hint)
        execute = pipe.execute

        def run(*args, **kwargs):
            if transaction:
                client.multis.append([(_text(c[0][0]), _text(c[0][1])) for c in pipe.command_stack])
            return execute(*args, **kwargs)

        pipe.execute = run
        return pipe

    client.pipeline = counted
    return cap.RedisCapabilityStore(client)


def _redis(store):
    return store._client


def _text(value):
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


def _keys(store, kind):
    """The store's keys of one Redis type, not counting the capability-use record's own."""
    r = _redis(store)
    return [_text(k) for k in r.keys("*")
            if not _text(k).startswith("nbcap-audit") and _text(r.type(k)) == kind]


def _data(store):
    """Every string entry: the handles' and the suspension markers, not the store's rebuild record."""
    return {k: _text(_redis(store).get(k)) for k in _keys(store, "string") if k != denials.STORE_REBUILT_KEY}


def _indexes(store):
    return {k: {_text(f): _text(v) for f, v in _redis(store).hgetall(k).items()}
            for k in _keys(store, "hash")}


def _put(store, key, value):
    _redis(store).set(key, value)


def _put_index(store, key, fields):
    _redis(store).hset(key, mapping=fields)


def _ttl(store, key):
    return _redis(store).ttl(key)


def _multis(store):
    """``[(command, key), ...]`` for each MULTI/EXEC the store's client executed."""
    return _redis(store).multis


def _stream(store):
    return testing.sync_audit_stream(_redis(store))


def _events(store, kind=None):
    return testing.sync_audit_events(_redis(store), kind)


def _mint(store, audience=cap.AUDIENCE_API, company_id=None):
    return cap.mint(
        store,
        user="alice",
        company_id=company_id or str(uuid.uuid4()),
        audience=audience,
        ttl_seconds=300,
    )


def test_mint_then_resolve_roundtrip():
    store = _store()
    cid = str(uuid.uuid4())
    handle = _mint(store, company_id=cid)

    binding = cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API)
    assert binding is not None
    assert binding.user == "alice"
    assert binding.company_id == cid
    assert binding.audience == cap.AUDIENCE_API
    assert binding.read_only is True


def test_handle_is_opaque_and_high_entropy():
    store = _store()
    h1, h2 = _mint(store), _mint(store)
    assert h1 != h2
    assert len(h1) >= 32
    # The handle reveals nothing about the binding.
    assert "alice" not in h1


def test_revoke_denies_next_use():
    store = _store()
    handle = _mint(store)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is not None
    cap.revoke(store, handle)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is None


def test_expiry_or_absence_denies():
    store = _store()
    assert cap.resolve(store, "never-minted", expected_audience=cap.AUDIENCE_API) is None
    assert cap.resolve(store, "", expected_audience=cap.AUDIENCE_API) is None


def test_audiences_are_not_interchangeable():
    store = _store()
    api_handle = _mint(store, audience=cap.AUDIENCE_API)
    sql_handle = _mint(store, audience=cap.AUDIENCE_SQL)

    # Each resolves only for its own audience (ADR-0015 dec 3).
    assert cap.resolve(store, api_handle, expected_audience=cap.AUDIENCE_API) is not None
    assert cap.resolve(store, api_handle, expected_audience=cap.AUDIENCE_SQL) is None
    assert cap.resolve(store, sql_handle, expected_audience=cap.AUDIENCE_SQL) is not None
    assert cap.resolve(store, sql_handle, expected_audience=cap.AUDIENCE_API) is None


def test_mint_rejects_unknown_audience():
    with pytest.raises(ValueError):
        cap.mint(_store(), user="a", company_id=str(uuid.uuid4()), audience="other", ttl_seconds=300)


@pytest.mark.parametrize("user,company", [("", "c"), ("u", "")])
def test_mint_requires_user_and_tenant(user, company):
    with pytest.raises(ValueError):
        cap.mint(_store(), user=user, company_id=company, audience=cap.AUDIENCE_API, ttl_seconds=300)


def test_store_holds_no_database_secret():
    # The stored record carries only binding facts, never a password/secret (ADR-0015 dec 7).
    store = _store()
    _mint(store)
    blob = " ".join(_data(store).values()).lower()
    assert "password" not in blob and "secret" not in blob


def test_tracking_audience_is_read_write_and_distinct():
    store = _store()
    h = _mint(store, audience=cap.AUDIENCE_TRACKING)
    # Tracking resolves only for tracking, and (unlike api/sql) is read-write within the tenant
    # namespace the gateway enforces (ADR-0019).
    b = cap.resolve(store, h, expected_audience=cap.AUDIENCE_TRACKING)
    assert b is not None and b.audience == cap.AUDIENCE_TRACKING
    assert b.read_only is False
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_SQL) is None
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_API) is None


def test_cold_audience_is_read_only_and_distinct():
    store = _store()
    h = _mint(store, audience=cap.AUDIENCE_COLD)
    # Cold resolves only for cold, and is read-only (training reads history, writes nothing —
    # ADR-0025 dec 5).
    b = cap.resolve(store, h, expected_audience=cap.AUDIENCE_COLD)
    assert b is not None and b.audience == cap.AUDIENCE_COLD
    assert b.read_only is True
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_TRACKING) is None
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_SQL) is None


def test_unknown_audience_rejected():
    store = _store()
    import pytest
    with pytest.raises(ValueError):
        cap.mint(store, user="u", company_id="c", audience="mlflow-direct", ttl_seconds=60)


# --- malformed store entries deny, never raise ----------------------------------------------------

@pytest.mark.parametrize("raw", [
    "[]", "null", '"api"', "7",
    json.dumps({"audience": "api", "user": "alice"}),
    json.dumps({"audience": "api", "company_id": str(uuid.uuid4())}),
])
def test_a_malformed_entry_denies(raw):
    store = _store()
    _put(store, "nbcap:h", raw)
    assert cap.resolve(store, "h", expected_audience=cap.AUDIENCE_API) is None


# --- lifecycle: a server's handles live and die with the server (ADR-0015 dec 2) -----------------

def _mint_for(store, server, user="alice", audience=cap.AUDIENCE_API):
    return cap.mint(store, user=user, company_id=str(uuid.uuid4()), audience=audience,
                    ttl_seconds=900, server_name=server)


def _live(store, handle, audience=cap.AUDIENCE_API):
    return cap.resolve(store, handle, expected_audience=audience) is not None


def test_revoking_a_server_revokes_exactly_its_handles():
    store = _store()
    mine = [_mint_for(store, ""), _mint_for(store, "", audience=cap.AUDIENCE_SQL)]
    other_server = _mint_for(store, "gpu")
    other_user = _mint_for(store, "", user="bob")

    assert cap.revoke_server(store, "alice", "") == 2

    assert not _live(store, mine[0]) and not _live(store, mine[1], cap.AUDIENCE_SQL)
    assert _live(store, other_server) and _live(store, other_user)
    assert cap.revoke_server(store, "alice", "") == 0  # idempotent


def test_revoking_a_user_revokes_every_server():
    store = _store()
    handles = [_mint_for(store, ""), _mint_for(store, "gpu")]
    bob = _mint_for(store, "", user="bob")
    assert cap.revoke_user(store, "alice") == 2
    assert not any(_live(store, h) for h in handles)
    assert _live(store, bob)


def test_refresh_extends_live_handles_and_never_revives_revoked_ones():
    store = _store()
    a, b = _mint_for(store, ""), _mint_for(store, "")
    cap.revoke(store, b)  # revoked out of band

    assert cap.refresh_server(store, "alice", "", 1800) == 1
    assert 1799 <= _ttl(store, f"nbcap:{a}") <= 1800
    assert f"nbcap:{b}" not in _data(store)            # not brought back
    assert b not in store.index_items("nbcap-user:alice")  # and dropped from the index


def test_refresh_reports_zero_once_a_logout_revoked_everything():
    store = _store()
    _mint_for(store, "")
    cap.revoke_user(store, "alice")
    assert cap.refresh_server(store, "alice", "", 900) == 0


def test_a_handle_minted_without_a_server_is_not_indexed():
    store = _store()
    cap.mint(store, user="alice", company_id=str(uuid.uuid4()), audience=cap.AUDIENCE_API, ttl_seconds=60)
    assert _indexes(store) == {}


def test_nothing_is_minted_for_a_suspended_tenant():
    # ADR-0033 dec 3: a suspended company is issued no new capabilities.
    store = _store()
    cid = str(uuid.uuid4())
    _put(store, f"nbcap-suspended:{cid}", "1")
    with pytest.raises(cap.CompanySuspended):
        cap.mint(store, user="u", company_id=cid, audience=cap.AUDIENCE_API, ttl_seconds=60, server_name="")
    assert [k for k in _data(store) if k.startswith("nbcap:")] == []
    assert _indexes(store) == {}


def test_a_suspended_tenants_handle_does_not_resolve():
    store = _store()
    cid = str(uuid.uuid4())
    handle = cap.mint(store, user="u", company_id=cid, audience=cap.AUDIENCE_API, ttl_seconds=60)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is not None
    _put(store, f"nbcap-suspended:{cid}", "1")
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is None


def test_a_store_that_lost_its_rebuild_record_mints_and_resolves_nothing():
    # ADR-0015 dec 8: recreated empty, the store cannot show which tenants are suspended.
    store = _store()
    cid = str(uuid.uuid4())
    handle = cap.mint(store, user="u", company_id=cid, audience=cap.AUDIENCE_API, ttl_seconds=60)
    _redis(store).delete(denials.STORE_REBUILT_KEY)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is None
    with pytest.raises(denials.StoreNotRebuilt):
        cap.mint(store, user="u", company_id=cid, audience=cap.AUDIENCE_API, ttl_seconds=60)


# --- the capability-use record (ADR-0042) ---------------------------------------------------------

def _entry(store, handle):
    return json.loads(_data(store)[f"nbcap:{handle}"])


def _no_handle_in_any_event(store, *handles):
    blob = json.dumps(_stream(store))
    return all(h not in blob for h in handles)


def test_a_mint_binds_a_handle_id_and_records_it_in_the_same_transaction():
    # dec 2-3: the entry, its index and its minted event are one write.
    store = _store()
    cid = str(uuid.uuid4())
    handle = cap.mint(store, user="alice", company_id=cid, audience=cap.AUDIENCE_SQL,
                      ttl_seconds=900, server_name="gpu")
    # One MULTI carrying the index, the entry and the record: they land together or not at all.
    assert _multis(store) == [[("HSET", "nbcap-user:alice"), ("EXPIRE", "nbcap-user:alice"),
                               ("SET", f"nbcap:{handle}"), ("XADD", audit.STREAM_KEY)]]
    (minted,) = _events(store, audit.MINTED)
    handle_id = _entry(store, handle)["handle_id"]
    assert str(uuid.UUID(handle_id)) == handle_id
    assert minted == {**minted, "plane": "hub", "user": "alice", "company_id": cid,
                      "audience": "sql", "handle_id": handle_id, "detail": "server=gpu"}
    assert _indexes(store)["nbcap-user:alice"] == {handle: "gpu"}
    assert _no_handle_in_any_event(store, handle)


def test_every_mint_gets_its_own_handle_id():
    store = _store()
    ids = {_entry(store, _mint(store))["handle_id"] for _ in range(20)}
    assert len(ids) == 20


def test_a_tenant_the_record_cannot_name_is_refused_before_anything_is_written():
    store = _store()
    with pytest.raises(ValueError):
        cap.mint(store, user="alice", company_id="not-a-uuid", audience=cap.AUDIENCE_API, ttl_seconds=60)
    assert _data(store) == {} and _stream(store) == []


def test_a_suspended_tenant_is_minted_nothing_and_recorded_nothing():
    store = _store()
    cid = str(uuid.uuid4())
    _put(store, f"nbcap-suspended:{cid}", "1")
    with pytest.raises(cap.CompanySuspended):
        cap.mint(store, user="u", company_id=cid, audience=cap.AUDIENCE_API, ttl_seconds=60)
    assert _stream(store) == []


def test_revoking_a_server_records_each_live_handle_with_its_facts():
    store = _store()
    cid = str(uuid.uuid4())
    handles = [cap.mint(store, user="alice", company_id=cid, audience=a, ttl_seconds=900,
                        server_name="") for a in (cap.AUDIENCE_API, cap.AUDIENCE_SQL)]
    ids = {_entry(store, h)["handle_id"]: _entry(store, h)["audience"] for h in handles}

    cap.revoke_server(store, "alice", "", reason=cap.REASON_RESPAWN)
    revoked = _events(store, audit.REVOKED)
    assert {e["handle_id"]: e["audience"] for e in revoked} == ids
    assert all(e["reason"] == "respawn" and e["user"] == "alice" and e["company_id"] == cid
               and e["plane"] == "hub" for e in revoked)
    assert _no_handle_in_any_event(store, *handles)


def test_a_revocation_is_recorded_only_when_it_removed_a_live_entry():
    # dec 3: an entry that expired on its own (or was revoked elsewhere) was not revoked here.
    store = _store()
    live, gone = _mint_for(store, ""), _mint_for(store, "")
    _redis(store).delete(f"nbcap:{gone}")  # expired, still indexed
    assert cap.revoke_server(store, "alice", "") == 2
    (revoked,) = _events(store, audit.REVOKED)
    assert revoked["reason"] == "server_stopped"
    assert cap.revoke_server(store, "alice", "") == 0
    assert cap.revoke(store, live) == 0
    assert len(_events(store, audit.REVOKED)) == 1


def test_revoking_a_user_records_each_handle_as_signed_out():
    store = _store()
    h1, h2 = _mint_for(store, ""), _mint_for(store, "gpu")
    assert cap.revoke_user(store, "alice") == 2
    revoked = _events(store, audit.REVOKED)
    assert len(revoked) == 2 and {e["reason"] for e in revoked} == {"user_signed_out"}
    assert _no_handle_in_any_event(store, h1, h2)


def test_revoking_one_handle_is_recorded():
    store = _store()
    handle = _mint(store)
    assert cap.revoke(store, handle) == 1
    (revoked,) = _events(store, audit.REVOKED)
    (minted,) = _events(store, audit.MINTED)
    assert revoked["handle_id"] == minted["handle_id"]
    assert revoked["reason"] == "revoked"


def test_a_handle_minted_before_the_record_is_revoked_and_recorded_without_an_id():
    # Entries minted before this change carry no handle_id; the revocation still happens and is
    # recorded with what the entry does say.
    store = _store()
    cid = str(uuid.uuid4())
    _put(store, "nbcap:old", json.dumps({"user": "alice", "company_id": cid, "audience": "api",
                                         "read_only": True}))
    _put_index(store, "nbcap-user:alice", {"old": ""})
    assert cap.resolve(store, "old", expected_audience=cap.AUDIENCE_API) is not None
    assert cap.revoke_server(store, "alice", "") == 1
    (revoked,) = _events(store, audit.REVOKED)
    assert revoked["handle_id"] is None and revoked["company_id"] == cid and revoked["user"] == "alice"
    assert "nbcap:old" not in _data(store)


def test_a_malformed_entry_is_still_revoked_and_recorded_by_the_indexed_user():
    store = _store()
    _put(store, "nbcap:bad", "[]")
    _put_index(store, "nbcap-user:alice", {"bad": ""})
    assert cap.revoke_server(store, "alice", "") == 1
    (revoked,) = _events(store, audit.REVOKED)
    assert revoked["user"] == "alice" and revoked["company_id"] is None
    assert "bad" not in json.dumps(_stream(store))


def test_environment_start_and_stop_are_recorded():
    store = _store()
    cid = str(uuid.uuid4())
    cap.record_environment_started(store, user="alice", company_id=cid, server_name="", profile="authoring")
    cap.record_environment_stopped(store, user="alice", company_id=cid, server_name="",
                                   reason=cap.REASON_SERVER_STOPPED)
    started, stopped = _events(store)
    assert (started["kind"], started["company_id"], started["detail"]) == (
        "environment_started", cid, "server= profile=authoring")
    assert (stopped["kind"], stopped["reason"], stopped["user"]) == (
        "environment_stopped", "server_stopped", "alice")


def test_an_environment_stop_with_no_known_tenant_is_still_recorded():
    store = _store()
    cap.record_environment_stopped(store, user="alice", company_id=None, server_name="",
                                   reason=cap.REASON_SERVER_STOPPED)
    (stopped,) = _events(store)
    assert stopped["company_id"] is None and stopped["user"] == "alice"

