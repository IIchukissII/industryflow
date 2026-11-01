# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Removes a deleted tenant from the capability store (ADR-0033 dec 4).

Suspension already ended every session and handle of the tenant's users (ADR-0033 dec 3); what can
remain is what was minted or cached since, or left behind by a failure: handles bound to the tenant,
its users' session and handle indexes, its feature snapshots and its latest-reading cache. All of it
goes. The suspension marker stays: with the registry's tombstone it is how every verifier keeps
refusing the tenant (ADR-0033 dec 8). So does the capability-use record's stream, which is not the
tenant's but evidence of what reached it (ADR-0042 dec 4): each handle removed here is recorded in it
as revoked, atomically with its removal (dec 1, 3).

The key layouts are the shared contract of industryflow_contracts.sessions (the ended-session record),
services/api_gateway/capability_auth.py, messaging/refresh_tokens.py, messaging/redis_client.py and
services/alert_service/worker/feature_store.py;
services/api_gateway/tests/test_suspension_contract.py holds this copy to the gateway's marker.
"""
from __future__ import annotations

import json
import uuid

from industryflow_contracts import capability_audit as audit
from industryflow_contracts import sessions as session_record

from .job import Removal

_KEY_PREFIX = "nbcap:"
_USER_INDEX_PREFIX = "nbcap-user:"
_UPLOAD_INDEX_PREFIX = "nbcap-upload-user:"
_SUSPENDED_PREFIX = "nbcap-suspended:"
_REFRESH_PREFIX = "refresh:"
_REFRESH_USER_PREFIX = "refresh-user:"
_FEATURES_PREFIX = "features:"
_LATEST_PREFIX = "sensor:latest:"


def _s(value):
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


def _bound_to(raw, company_id: str) -> bool:
    try:
        record = json.loads(_s(raw))
    except (TypeError, ValueError):
        return False
    return isinstance(record, dict) and str(record.get("company_id", "")).lower() == company_id


def _entry_facts(raw) -> dict:
    """What a handle's entry says that a record may carry: user, tenant, audience and the handle's
    non-secret identifier, each only when well-formed — never the handle (ADR-0042 dec 2)."""
    try:
        record = json.loads(_s(raw)) if raw is not None else {}
    except (TypeError, ValueError):
        return {}
    if not isinstance(record, dict):
        return {}
    facts = {}
    for name in ("company_id", "handle_id"):
        try:
            facts[name] = str(uuid.UUID(str(record.get(name) or "")))
        except ValueError:
            pass
    if isinstance(record.get("user"), str):
        facts["user"] = record["user"]
    if record.get("audience") in audit.AUDIENCES:
        facts["audience"] = record["audience"]
    return facts


class CapabilityStore:
    def __init__(self, redis):
        self._redis = redis

    def _matching(self, pattern: str, company_id: str) -> list:
        """Keys under ``pattern`` whose JSON value is bound to the tenant."""
        found = []
        for key in self._redis.scan_iter(match=pattern, count=500):
            key = _s(key)
            if key.startswith(_SUSPENDED_PREFIX):
                continue  # the marker stays (ADR-0033 dec 8)
            if _bound_to(self._redis.get(key), company_id):
                found.append(key)
        return found

    def purge(self, company_id: str, user_ids: list, requested_at=None) -> Removal:
        company_id = company_id.lower()
        handles = set(self._matching(f"{_KEY_PREFIX}*", company_id))
        indexes, sessions = [], []
        for user in user_ids:
            index, upload = f"{_USER_INDEX_PREFIX}{user}", f"{_UPLOAD_INDEX_PREFIX}{user}"
            handles.update(f"{_KEY_PREFIX}{_s(h)}" for h in self._redis.hkeys(index))
            handles.update(f"{_KEY_PREFIX}{_s(h)}" for h in self._redis.smembers(upload))
            refresh_index = f"{_REFRESH_USER_PREFIX}{user}"
            sessions.extend(f"{_REFRESH_PREFIX}{_s(t)}" for t in self._redis.smembers(refresh_index))
            indexes += [index, upload, refresh_index, session_record.user_ended_key(user)]
        features = [_s(k) for k in self._redis.scan_iter(match=f"{_FEATURES_PREFIX}{company_id}:*", count=500)]
        latest = self._matching(f"{_LATEST_PREFIX}*", company_id)
        for key in sorted(handles):
            self._revoke(key, company_id)
        keys = sessions + indexes + features + latest
        for start in range(0, len(keys), 500):
            self._redis.delete(*keys[start:start + 500])
        # A lost marker would let a verifier honour a stray handle of the tenant: put it back.
        self._redis.set(f"{_SUSPENDED_PREFIX}{company_id}", "1")
        return Removal(detail={"handles": len(handles), "sessions": len(sessions), "user_indexes": len(indexes),
                               "feature_snapshots": len(features), "latest_readings": len(latest)})

    def _revoke(self, key: str, company_id: str) -> None:
        """Remove one handle, recording it as revoked only if this removed it (ADR-0042 dec 3): a
        repeat of the job, or an entry that expired on its own, records nothing."""
        facts = _entry_facts(self._redis.get(key))
        fields = audit.event(audit.REVOKED, plane="tenant_delete", reason="tenant_deleted",
                             user=facts.get("user"), company_id=facts.get("company_id", company_id),
                             audience=facts.get("audience"), handle_id=facts.get("handle_id"))
        keys, args = audit.delete_recorded_args(key, fields)
        self._redis.eval(audit.DELETE_RECORDED_SCRIPT, len(keys), *keys, *args)
