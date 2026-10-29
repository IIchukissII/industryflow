# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A deleted tenant leaves nothing in the capability store but its suspension marker (ADR-0033 dec 4, 8)."""
import json
import uuid

import fakeredis
from industryflow_contracts import capability_audit as audit

from tenant_delete.capability_store import CapabilityStore

A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
B = "11111111-2222-3333-4444-555555555555"
U = "aaaaaaaa-0000-0000-0000-000000000001"
HID_A, HID_INDEXED, HID_B = (str(uuid.uuid4()) for _ in range(3))


def _store():
    """A real store's semantics (fakeredis, with the contract's Lua), seeded with a tenant A to
    delete beside a tenant B to keep."""
    redis = fakeredis.FakeRedis()
    kv = {
        "nbcap:h-a": json.dumps({"company_id": A, "user": U, "audience": "tracking", "handle_id": HID_A}),
        "nbcap:h-b": json.dumps({"company_id": B, "audience": "api", "handle_id": HID_B}),
        # Minted before the record existed: no handle_id, still removed and recorded.
        "nbcap:h-legacy": json.dumps({"company_id": A, "user": U, "audience": "sql"}),
        "nbcap:h-indexed": json.dumps({"company_id": A, "user": U, "audience": "upload",
                                       "handle_id": HID_INDEXED}),
        "refresh:t1": json.dumps({"user_id": U}),
        f"sessions-ended-at:{U}": "1760000000",
        f"features:{A}:e1:temperature": "[]",
        f"features:{B}:e1:temperature": "[]",
        "sensor:latest:s-a": json.dumps({"company_id": A, "value": 1}),
        "sensor:latest:s-b": json.dumps({"company_id": B, "value": 2}),
        f"nbcap-suspended:{B}": "1",
    }
    for k, v in kv.items():
        redis.set(k, v)
    redis.hset(f"nbcap-user:{U}", "h-indexed", "server")
    # An index member whose entry already expired on its own: nobody revoked it.
    redis.sadd(f"nbcap-upload-user:{U}", "h-expired")
    redis.sadd(f"refresh-user:{U}", "t1")
    return redis


def _keys(redis):
    return {k.decode() for k in redis.keys("*")}


def _events(redis):
    return [audit.decode(f) for _id, f in redis.xrange(audit.STREAM_KEY)]


def test_everything_of_the_tenant_goes_and_nothing_of_another():
    redis = _store()
    removal = CapabilityStore(redis).purge(A, [U])
    assert _keys(redis) == {"nbcap:h-b", f"features:{B}:e1:temperature", "sensor:latest:s-b",
                            f"nbcap-suspended:{B}", f"nbcap-suspended:{A}", audit.STREAM_KEY}
    assert removal.detail == {"handles": 4, "sessions": 1, "user_indexes": 4,
                              "feature_snapshots": 1, "latest_readings": 1}


def test_the_suspension_marker_stays_and_is_restored_if_lost():
    redis = _store()
    CapabilityStore(redis).purge(A, [U])
    assert redis.get(f"nbcap-suspended:{A}") == b"1"


def test_a_repeat_removes_nothing_more():
    redis = _store()
    store = CapabilityStore(redis)
    store.purge(A, [U])
    recorded = len(_events(redis))
    again = store.purge(A, [U])
    assert again.detail["handles"] == 0 and again.detail["feature_snapshots"] == 0
    assert len(_events(redis)) == recorded, "a repeat revokes nothing, so records nothing"


def test_each_removed_handle_is_recorded_as_revoked_and_the_record_stays():
    """ADR-0042 dec 1, 3, 4: every handle this removes is recorded with what its entry named, in the
    same step as its removal; an entry that was already gone records nothing; the record's stream is
    not the tenant's and outlives it."""
    redis = _store()
    CapabilityStore(redis).purge(A, [U])

    events = _events(redis)
    assert {e["kind"] for e in events} == {audit.REVOKED}
    assert {(e["plane"], e["reason"]) for e in events} == {("tenant_delete", "tenant_deleted")}
    assert all(e["company_id"] == A for e in events)
    assert sorted((e["handle_id"] or "", e["audience"]) for e in events) == sorted(
        [(HID_A, "tracking"), (HID_INDEXED, "upload"), ("", "sql")])
    assert {e["user"] for e in events} == {U}
    assert HID_B not in {e["handle_id"] for e in events}, "another tenant's handle is not revoked"
    for _id, fields in redis.xrange(audit.STREAM_KEY):
        assert not any(v.startswith(b"h-") for v in fields.values()), "a bearer handle entered the record"
