# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A deleted tenant leaves nothing in the capability store but its suspension marker (ADR-0033 dec 4, 8)."""
import fnmatch
import json

from tenant_delete.capability_store import CapabilityStore

A = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
B = "11111111-2222-3333-4444-555555555555"
U = "aaaaaaaa-0000-0000-0000-000000000001"


class FakeRedis:
    def __init__(self, kv=None, hashes=None, sets=None):
        self.kv, self.hashes, self.sets = dict(kv or {}), dict(hashes or {}), dict(sets or {})

    def _all(self):
        return list(self.kv) + list(self.hashes) + list(self.sets)

    def scan_iter(self, match, count=None):
        return [k.encode() for k in self._all() if fnmatch.fnmatchcase(k, match)]

    def get(self, key):
        v = self.kv.get(key)
        return v.encode() if isinstance(v, str) else v

    def hkeys(self, key):
        return [k.encode() for k in self.hashes.get(key, {})]

    def smembers(self, key):
        return {m.encode() for m in self.sets.get(key, set())}

    def delete(self, *keys):
        for k in keys:
            for d in (self.kv, self.hashes, self.sets):
                d.pop(k, None)

    def set(self, key, value):
        self.kv[key] = value

    def exists(self, key):
        return int(key in self._all())


def _store():
    return FakeRedis(
        kv={
            "nbcap:h-a": json.dumps({"company_id": A, "user": U}),
            "nbcap:h-b": json.dumps({"company_id": B}),
            "nbcap:h-indexed": json.dumps({"company_id": A}),
            "refresh:t1": json.dumps({"user_id": U}),
            f"sessions-ended-at:{U}": "1760000000",
            f"features:{A}:e1:temperature": "[]",
            f"features:{B}:e1:temperature": "[]",
            "sensor:latest:s-a": json.dumps({"company_id": A, "value": 1}),
            "sensor:latest:s-b": json.dumps({"company_id": B, "value": 2}),
            f"nbcap-suspended:{B}": "1",
        },
        hashes={f"nbcap-user:{U}": {"h-indexed": "server"}},
        sets={f"refresh-user:{U}": {"t1"}, f"nbcap-upload-user:{U}": set()},
    )


def test_everything_of_the_tenant_goes_and_nothing_of_another():
    redis = _store()
    removal = CapabilityStore(redis).purge(A, [U])
    left = set(redis._all())
    assert left == {"nbcap:h-b", f"features:{B}:e1:temperature", "sensor:latest:s-b",
                    f"nbcap-suspended:{B}", f"nbcap-suspended:{A}"}
    assert removal.detail == {"handles": 2, "sessions": 1, "user_indexes": 4,
                              "feature_snapshots": 1, "latest_readings": 1}


def test_the_suspension_marker_stays_and_is_restored_if_lost():
    redis = _store()
    CapabilityStore(redis).purge(A, [U])
    assert redis.kv[f"nbcap-suspended:{A}"] == "1"


def test_a_repeat_removes_nothing_more():
    redis = _store()
    store = CapabilityStore(redis)
    store.purge(A, [U])
    again = store.purge(A, [U])
    assert again.detail["handles"] == 0 and again.detail["feature_snapshots"] == 0
