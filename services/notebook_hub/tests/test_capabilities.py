# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for capability mint/resolve/revoke (ADR-0015) and their record (ADR-0042), against an
in-memory fake store.

The fake interprets the record contract's two scripts by identity and refuses any other, so the hub
cannot start evaluating a script nobody modelled and pass on an invented answer. What the scripts
really do is proven in a Lua interpreter (libs/industryflow_contracts/tests/test_capability_audit.py),
and the Redis adapter's transaction against a real one (test_redis_capability_store.py).
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import capabilities as cap  # noqa: E402
from industryflow_contracts import capability_audit as audit  # noqa: E402


class FakeStore:
    """In-memory CapabilityStore; records TTLs but never expires (that is the store's job).
    Appended events land in ``stream``; ``transactions`` counts the writes that were one MULTI."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.indexes: dict[str, dict[str, str]] = {}
        self.ttls: dict[str, int] = {}
        self.stream: list[dict] = []
        self.markers: set = set()
        self.transactions = 0

    def set_recorded(self, key, value, ttl_seconds, event, index=None):
        self.transactions += 1
        if index is not None:
            index_key, field, index_value = index
            self.indexes.setdefault(index_key, {})[field] = index_value
            self.ttls[index_key] = ttl_seconds
        self.data[key] = value
        self.ttls[key] = ttl_seconds
        self.stream.append(dict(event))

    def eval(self, script, keys, args):
        if script == audit.DELETE_RECORDED_SCRIPT:
            removed = int(keys[0] in self.data)
            self.delete(keys[0])
            if removed:
                self.stream.append(_pairs(args[1:]))
            return removed
        if script == audit.FIRST_USE_SCRIPT:
            if keys[0] in self.markers:
                return 0
            self.markers.add(keys[0])
            self.stream.append(_pairs(args[2:]))
            return 1
        raise AssertionError("a script the capability-audit fake does not model")

    def append(self, event):
        self.stream.append(dict(event))

    def events(self, kind=None):
        return [audit.decode(e) for e in self.stream if kind is None or e.get("kind") == kind]

    def get(self, key):
        return self.data.get(key)

    def delete(self, *keys):
        for key in keys:
            self.data.pop(key, None)
            self.indexes.pop(key, None)
            self.ttls.pop(key, None)

    def expire(self, key, ttl_seconds):
        if key not in self.data and key not in self.indexes:
            return False
        self.ttls[key] = ttl_seconds
        return True

    def index_items(self, key):
        return dict(self.indexes.get(key, {}))

    def index_remove(self, key, *fields):
        for field in fields:
            self.indexes.get(key, {}).pop(field, None)


def _pairs(flat):
    return dict(zip(flat[0::2], flat[1::2]))


def _mint(store, audience=cap.AUDIENCE_API, company_id=None):
    return cap.mint(
        store,
        user="alice",
        company_id=company_id or str(uuid.uuid4()),
        audience=audience,
        ttl_seconds=300,
    )


def test_mint_then_resolve_roundtrip():
    store = FakeStore()
    cid = str(uuid.uuid4())
    handle = _mint(store, company_id=cid)

    binding = cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API)
    assert binding is not None
    assert binding.user == "alice"
    assert binding.company_id == cid
    assert binding.audience == cap.AUDIENCE_API
    assert binding.read_only is True


def test_handle_is_opaque_and_high_entropy():
    store = FakeStore()
    h1, h2 = _mint(store), _mint(store)
    assert h1 != h2
    assert len(h1) >= 32
    # The handle reveals nothing about the binding.
    assert "alice" not in h1


def test_revoke_denies_next_use():
    store = FakeStore()
    handle = _mint(store)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is not None
    cap.revoke(store, handle)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is None


def test_expiry_or_absence_denies():
    store = FakeStore()
    assert cap.resolve(store, "never-minted", expected_audience=cap.AUDIENCE_API) is None
    assert cap.resolve(store, "", expected_audience=cap.AUDIENCE_API) is None


def test_audiences_are_not_interchangeable():
    store = FakeStore()
    api_handle = _mint(store, audience=cap.AUDIENCE_API)
    sql_handle = _mint(store, audience=cap.AUDIENCE_SQL)

    # Each resolves only for its own audience (ADR-0015 dec 3).
    assert cap.resolve(store, api_handle, expected_audience=cap.AUDIENCE_API) is not None
    assert cap.resolve(store, api_handle, expected_audience=cap.AUDIENCE_SQL) is None
    assert cap.resolve(store, sql_handle, expected_audience=cap.AUDIENCE_SQL) is not None
    assert cap.resolve(store, sql_handle, expected_audience=cap.AUDIENCE_API) is None


def test_mint_rejects_unknown_audience():
    with pytest.raises(ValueError):
        cap.mint(FakeStore(), user="a", company_id=str(uuid.uuid4()), audience="other", ttl_seconds=300)


@pytest.mark.parametrize("user,company", [("", "c"), ("u", "")])
def test_mint_requires_user_and_tenant(user, company):
    with pytest.raises(ValueError):
        cap.mint(FakeStore(), user=user, company_id=company, audience=cap.AUDIENCE_API, ttl_seconds=300)


def test_store_holds_no_database_secret():
    # The stored record carries only binding facts, never a password/secret (ADR-0015 dec 7).
    store = FakeStore()
    _mint(store)
    blob = " ".join(store.data.values()).lower()
    assert "password" not in blob and "secret" not in blob


def test_tracking_audience_is_read_write_and_distinct():
    store = FakeStore()
    h = _mint(store, audience=cap.AUDIENCE_TRACKING)
    # Tracking resolves only for tracking, and (unlike api/sql) is read-write within the tenant
    # namespace the gateway enforces (ADR-0019).
    b = cap.resolve(store, h, expected_audience=cap.AUDIENCE_TRACKING)
    assert b is not None and b.audience == cap.AUDIENCE_TRACKING
    assert b.read_only is False
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_SQL) is None
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_API) is None


def test_cold_audience_is_read_only_and_distinct():
    store = FakeStore()
    h = _mint(store, audience=cap.AUDIENCE_COLD)
    # Cold resolves only for cold, and is read-only (training reads history, writes nothing —
    # ADR-0025 dec 5).
    b = cap.resolve(store, h, expected_audience=cap.AUDIENCE_COLD)
    assert b is not None and b.audience == cap.AUDIENCE_COLD
    assert b.read_only is True
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_TRACKING) is None
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_SQL) is None


def test_unknown_audience_rejected():
    store = FakeStore()
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
    store = FakeStore()
    store.data["nbcap:h"] = raw
    assert cap.resolve(store, "h", expected_audience=cap.AUDIENCE_API) is None


# --- lifecycle: a server's handles live and die with the server (ADR-0015 dec 2) -----------------

def _mint_for(store, server, user="alice", audience=cap.AUDIENCE_API):
    return cap.mint(store, user=user, company_id=str(uuid.uuid4()), audience=audience,
                    ttl_seconds=900, server_name=server)


def _live(store, handle, audience=cap.AUDIENCE_API):
    return cap.resolve(store, handle, expected_audience=audience) is not None


def test_revoking_a_server_revokes_exactly_its_handles():
    store = FakeStore()
    mine = [_mint_for(store, ""), _mint_for(store, "", audience=cap.AUDIENCE_SQL)]
    other_server = _mint_for(store, "gpu")
    other_user = _mint_for(store, "", user="bob")

    assert cap.revoke_server(store, "alice", "") == 2

    assert not _live(store, mine[0]) and not _live(store, mine[1], cap.AUDIENCE_SQL)
    assert _live(store, other_server) and _live(store, other_user)
    assert cap.revoke_server(store, "alice", "") == 0  # idempotent


def test_revoking_a_user_revokes_every_server():
    store = FakeStore()
    handles = [_mint_for(store, ""), _mint_for(store, "gpu")]
    bob = _mint_for(store, "", user="bob")
    assert cap.revoke_user(store, "alice") == 2
    assert not any(_live(store, h) for h in handles)
    assert _live(store, bob)


def test_refresh_extends_live_handles_and_never_revives_revoked_ones():
    store = FakeStore()
    a, b = _mint_for(store, ""), _mint_for(store, "")
    cap.revoke(store, b)  # revoked out of band

    assert cap.refresh_server(store, "alice", "", 1800) == 1
    assert store.ttls[f"nbcap:{a}"] == 1800
    assert f"nbcap:{b}" not in store.data             # not brought back
    assert b not in store.index_items("nbcap-user:alice")  # and dropped from the index


def test_refresh_reports_zero_once_a_logout_revoked_everything():
    store = FakeStore()
    _mint_for(store, "")
    cap.revoke_user(store, "alice")
    assert cap.refresh_server(store, "alice", "", 900) == 0


def test_a_handle_minted_without_a_server_is_not_indexed():
    store = FakeStore()
    cap.mint(store, user="alice", company_id=str(uuid.uuid4()), audience=cap.AUDIENCE_API, ttl_seconds=60)
    assert store.indexes == {}


def test_nothing_is_minted_for_a_suspended_tenant():
    # ADR-0033 dec 3: a suspended company is issued no new capabilities.
    store = FakeStore()
    cid = str(uuid.uuid4())
    store.data[f"nbcap-suspended:{cid}"] = "1"
    with pytest.raises(cap.CompanySuspended):
        cap.mint(store, user="u", company_id=cid, audience=cap.AUDIENCE_API, ttl_seconds=60, server_name="")
    assert [k for k in store.data if k.startswith("nbcap:")] == []
    assert store.indexes == {}


def test_a_suspended_tenants_handle_does_not_resolve():
    store = FakeStore()
    cid = str(uuid.uuid4())
    handle = cap.mint(store, user="u", company_id=cid, audience=cap.AUDIENCE_API, ttl_seconds=60)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is not None
    store.data[f"nbcap-suspended:{cid}"] = "1"
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is None


# --- the capability-use record (ADR-0042) ---------------------------------------------------------

def _entry(store, handle):
    return json.loads(store.data[f"nbcap:{handle}"])


def _no_handle_in_any_event(store, *handles):
    blob = json.dumps(store.stream)
    return all(h not in blob for h in handles)


def test_a_mint_binds_a_handle_id_and_records_it_in_the_same_transaction():
    # dec 2-3: the entry, its index and its minted event are one write.
    store = FakeStore()
    cid = str(uuid.uuid4())
    handle = cap.mint(store, user="alice", company_id=cid, audience=cap.AUDIENCE_SQL,
                      ttl_seconds=900, server_name="gpu")
    assert store.transactions == 1
    (minted,) = store.events(audit.MINTED)
    handle_id = _entry(store, handle)["handle_id"]
    assert str(uuid.UUID(handle_id)) == handle_id
    assert minted == {**minted, "plane": "hub", "user": "alice", "company_id": cid,
                      "audience": "sql", "handle_id": handle_id, "detail": "server=gpu"}
    assert store.indexes["nbcap-user:alice"] == {handle: "gpu"}
    assert _no_handle_in_any_event(store, handle)


def test_every_mint_gets_its_own_handle_id():
    store = FakeStore()
    ids = {_entry(store, _mint(store))["handle_id"] for _ in range(20)}
    assert len(ids) == 20


def test_a_tenant_the_record_cannot_name_is_refused_before_anything_is_written():
    store = FakeStore()
    with pytest.raises(ValueError):
        cap.mint(store, user="alice", company_id="not-a-uuid", audience=cap.AUDIENCE_API, ttl_seconds=60)
    assert store.data == {} and store.stream == []


def test_a_suspended_tenant_is_minted_nothing_and_recorded_nothing():
    store = FakeStore()
    cid = str(uuid.uuid4())
    store.data[f"nbcap-suspended:{cid}"] = "1"
    with pytest.raises(cap.CompanySuspended):
        cap.mint(store, user="u", company_id=cid, audience=cap.AUDIENCE_API, ttl_seconds=60)
    assert store.stream == []


def test_revoking_a_server_records_each_live_handle_with_its_facts():
    store = FakeStore()
    cid = str(uuid.uuid4())
    handles = [cap.mint(store, user="alice", company_id=cid, audience=a, ttl_seconds=900,
                        server_name="") for a in (cap.AUDIENCE_API, cap.AUDIENCE_SQL)]
    ids = {_entry(store, h)["handle_id"]: _entry(store, h)["audience"] for h in handles}

    cap.revoke_server(store, "alice", "", reason=cap.REASON_RESPAWN)
    revoked = store.events(audit.REVOKED)
    assert {e["handle_id"]: e["audience"] for e in revoked} == ids
    assert all(e["reason"] == "respawn" and e["user"] == "alice" and e["company_id"] == cid
               and e["plane"] == "hub" for e in revoked)
    assert _no_handle_in_any_event(store, *handles)


def test_a_revocation_is_recorded_only_when_it_removed_a_live_entry():
    # dec 3: an entry that expired on its own (or was revoked elsewhere) was not revoked here.
    store = FakeStore()
    live, gone = _mint_for(store, ""), _mint_for(store, "")
    del store.data[f"nbcap:{gone}"]  # expired, still indexed
    assert cap.revoke_server(store, "alice", "") == 2
    (revoked,) = store.events(audit.REVOKED)
    assert revoked["reason"] == "server_stopped"
    assert cap.revoke_server(store, "alice", "") == 0
    assert cap.revoke(store, live) == 0
    assert len(store.events(audit.REVOKED)) == 1


def test_revoking_a_user_records_each_handle_as_signed_out():
    store = FakeStore()
    h1, h2 = _mint_for(store, ""), _mint_for(store, "gpu")
    assert cap.revoke_user(store, "alice") == 2
    revoked = store.events(audit.REVOKED)
    assert len(revoked) == 2 and {e["reason"] for e in revoked} == {"user_signed_out"}
    assert _no_handle_in_any_event(store, h1, h2)


def test_revoking_one_handle_is_recorded():
    store = FakeStore()
    handle = _mint(store)
    assert cap.revoke(store, handle) == 1
    (revoked,) = store.events(audit.REVOKED)
    (minted,) = store.events(audit.MINTED)
    assert revoked["handle_id"] == minted["handle_id"]
    assert revoked["reason"] == "revoked"


def test_a_handle_minted_before_the_record_is_revoked_and_recorded_without_an_id():
    # Entries minted before this change carry no handle_id; the revocation still happens and is
    # recorded with what the entry does say.
    store = FakeStore()
    cid = str(uuid.uuid4())
    store.data["nbcap:old"] = json.dumps({"user": "alice", "company_id": cid, "audience": "api",
                                          "read_only": True})
    store.indexes["nbcap-user:alice"] = {"old": ""}
    assert cap.resolve(store, "old", expected_audience=cap.AUDIENCE_API) is not None
    assert cap.revoke_server(store, "alice", "") == 1
    (revoked,) = store.events(audit.REVOKED)
    assert revoked["handle_id"] is None and revoked["company_id"] == cid and revoked["user"] == "alice"
    assert "nbcap:old" not in store.data


def test_a_malformed_entry_is_still_revoked_and_recorded_by_the_indexed_user():
    store = FakeStore()
    store.data["nbcap:bad"] = "[]"
    store.indexes["nbcap-user:alice"] = {"bad": ""}
    assert cap.revoke_server(store, "alice", "") == 1
    (revoked,) = store.events(audit.REVOKED)
    assert revoked["user"] == "alice" and revoked["company_id"] is None
    assert "bad" not in json.dumps(store.stream)


def test_environment_start_and_stop_are_recorded():
    store = FakeStore()
    cid = str(uuid.uuid4())
    cap.record_environment_started(store, user="alice", company_id=cid, server_name="", profile="authoring")
    cap.record_environment_stopped(store, user="alice", company_id=cid, server_name="",
                                   reason=cap.REASON_SERVER_STOPPED)
    started, stopped = store.events()
    assert (started["kind"], started["company_id"], started["detail"]) == (
        "environment_started", cid, "server= profile=authoring")
    assert (stopped["kind"], stopped["reason"], stopped["user"]) == (
        "environment_stopped", "server_stopped", "alice")


def test_an_environment_stop_with_no_known_tenant_is_still_recorded():
    store = FakeStore()
    cap.record_environment_stopped(store, user="alice", company_id=None, server_name="",
                                   reason=cap.REASON_SERVER_STOPPED)
    (stopped,) = store.events()
    assert stopped["company_id"] is None and stopped["user"] == "alice"


def test_the_fake_refuses_a_script_nobody_modelled():
    with pytest.raises(AssertionError):
        FakeStore().eval("return 1", [], [])
