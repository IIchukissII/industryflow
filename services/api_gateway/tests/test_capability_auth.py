# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Verifier-side capability resolution tests (ADR-0015), with a fake async store."""
import json
import uuid

import pytest

from capability_audit_fake import AuditScripts
from industryflow_contracts import capability_audit as audit


class FakeAsyncStore:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    async def get(self, key):
        return self.mapping.get(key)


def _record(company_id, audience="api", user="u", read_only=True):
    return json.dumps(
        {"user": user, "company_id": company_id, "audience": audience, "read_only": read_only}
    )


@pytest.mark.asyncio
async def test_resolve_roundtrip():
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    cid = str(uuid.uuid4())
    store = FakeAsyncStore({f"{_KEY_PREFIX}h1": _record(cid)})
    binding = await resolve_capability(store.get, "h1", expected_audience=AUDIENCE_API)
    assert binding is not None
    assert binding.company_id == cid
    assert binding.audience == AUDIENCE_API
    assert binding.read_only is True


@pytest.mark.asyncio
async def test_resolve_accepts_bytes_value():
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    cid = str(uuid.uuid4())
    store = FakeAsyncStore({f"{_KEY_PREFIX}h1": _record(cid).encode()})
    binding = await resolve_capability(store.get, "h1", expected_audience=AUDIENCE_API)
    assert binding is not None and binding.company_id == cid


@pytest.mark.asyncio
async def test_wrong_audience_denies():
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    store = FakeAsyncStore({f"{_KEY_PREFIX}h1": _record(str(uuid.uuid4()), audience="sql")})
    assert await resolve_capability(store.get, "h1", expected_audience=AUDIENCE_API) is None


@pytest.mark.asyncio
async def test_absent_empty_and_malformed_deny():
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    store = FakeAsyncStore({f"{_KEY_PREFIX}bad": "not-json"})
    assert await resolve_capability(store.get, "missing", expected_audience=AUDIENCE_API) is None
    assert await resolve_capability(store.get, "", expected_audience=AUDIENCE_API) is None
    assert await resolve_capability(store.get, "bad", expected_audience=AUDIENCE_API) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [
    "[]", "null", '"api"', "3",                                          # JSON, but not an object
    json.dumps({"user": "u", "audience": "api"}),                         # no tenant
    json.dumps({"user": "u", "audience": "api", "company_id": None}),     # null tenant
    json.dumps({"user": "u", "audience": "api", "company_id": "tenant_x; --"}),  # not a UUID
])
async def test_a_malformed_store_entry_denies_rather_than_raising(raw):
    """A malformed entry must be a refusal (401), never a 500 from a KeyError/AttributeError."""
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    store = FakeAsyncStore({f"{_KEY_PREFIX}h": raw})
    assert await resolve_capability(store.get, "h", expected_audience=AUDIENCE_API) is None


# --- platform logout revokes the user's notebook capabilities (ADR-0012 dec 6) --------------------

class FakeRedis(AuditScripts):
    """Strings and hashes, async, decode_responses=True — as messaging.redis_client connects."""

    def __init__(self):
        self.strings, self.hashes, self.sets = {}, {}, {}

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None):
        self.strings[key] = value

    async def getdel(self, key):
        return self.strings.pop(key, None)

    async def srem(self, key, *members):
        # The refresh-token per-user index (messaging.refresh_tokens); not modelled here.
        return 0

    async def hkeys(self, key):
        return list(self.hashes.get(key, {}))

    async def delete(self, *keys):
        n = 0
        for key in keys:
            n += int(self.strings.pop(key, None) is not None or self.hashes.pop(key, None) is not None
                     or self.sets.pop(key, None) is not None)
        return n


def _seed(redis, user, handles):
    for h in handles:
        redis.strings[f"nbcap:{h}"] = _record(str(uuid.uuid4()))
    redis.hashes[f"nbcap-user:{user}"] = {h: "" for h in handles}


@pytest.mark.asyncio
async def test_revoking_a_user_revokes_exactly_their_handles():
    from capability_auth import revoke_user_capabilities

    redis = FakeRedis()
    _seed(redis, "alice", ["a1", "a2"])
    _seed(redis, "bob", ["b1"])

    assert await revoke_user_capabilities(redis, "alice") == 2
    assert set(redis.strings) == {"nbcap:b1"}
    assert set(redis.hashes) == {"nbcap-user:bob"}


@pytest.mark.asyncio
async def test_logout_revokes_the_users_notebook_capabilities(monkeypatch):
    from types import SimpleNamespace

    from messaging.redis_client import redis_client
    from routers import auth_tokens

    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "redis", redis)
    redis.strings["refresh:tok"] = "alice"
    _seed(redis, "alice", ["a1", "a2"])
    _seed(redis, "bob", ["b1"])

    request = SimpleNamespace(cookies={auth_tokens.REFRESH_COOKIE: "tok"})
    from fastapi import Response

    await auth_tokens.logout(request, Response(), None)

    assert "refresh:tok" not in redis.strings
    assert set(redis.strings) == {"nbcap:b1"}  # alice's handles are gone, bob's are not


@pytest.mark.asyncio
async def test_logout_with_an_unknown_refresh_token_revokes_nothing(monkeypatch):
    from types import SimpleNamespace

    from fastapi import Response
    from messaging.redis_client import redis_client
    from routers import auth_tokens

    redis = FakeRedis()
    monkeypatch.setattr(redis_client, "redis", redis)
    _seed(redis, "alice", ["a1"])

    await auth_tokens.logout(SimpleNamespace(cookies={auth_tokens.REFRESH_COOKIE: "stale"}), Response(), None)
    assert set(redis.strings) == {"nbcap:a1"}


@pytest.mark.asyncio
async def test_revoking_a_user_also_revokes_their_upload_handles():
    # The model-upload minter indexes each handle it demand-mints under its user, so ending the
    # user's sessions ends those too (ADR-0004 dec 2).
    from capability_auth import revoke_user_capabilities

    redis = FakeRedis()
    _seed(redis, "alice", ["a1"])
    redis.strings["nbcap:u1"] = _record(str(uuid.uuid4()), audience="upload")
    redis.sets["nbcap-upload-user:alice"] = {"u1"}
    redis.strings["nbcap:u2"] = _record(str(uuid.uuid4()), audience="upload")
    redis.sets["nbcap-upload-user:bob"] = {"u2"}

    assert await revoke_user_capabilities(redis, "alice") == 2
    assert set(redis.strings) == {"nbcap:u2"}
    assert set(redis.sets) == {"nbcap-upload-user:bob"}


# --- the capability-use record (ADR-0042) ------------------------------------------------------

def _audited(company_id, handle_id, audience="api", user="alice"):
    return json.dumps({"user": user, "company_id": company_id, "audience": audience, "read_only": True,
                       "handle_id": handle_id})


@pytest.mark.asyncio
async def test_a_revocation_is_recorded_with_the_handles_identifier_never_the_handle():
    from capability_auth import revoke_user_capabilities

    redis = FakeRedis()
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    redis.strings["nbcap:secret-a1"] = _audited(cid, hid)
    redis.hashes["nbcap-user:alice"] = {"secret-a1": "", "expired-a2": ""}

    assert await revoke_user_capabilities(redis, "alice") == 1
    [event] = redis.events("revoked")
    assert (event["handle_id"], event["company_id"], event["reason"]) == (hid, cid, "user_signed_out")
    assert not any("secret-a1" in v for e in redis.stream for v in e.values())
    assert len(redis.stream) == 1  # the handle that had already expired was revoked by no one


@pytest.mark.asyncio
async def test_first_use_is_recorded_once_and_an_unknown_value_once_per_window():
    from capability_auth import AUDIENCE_API, record_resolution, resolve_capability_outcome

    redis = FakeRedis()
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    redis.strings["nbcap:h"] = _audited(cid, hid)
    redis.strings["nbcap:s"] = _audited(cid, str(uuid.uuid4()), audience="sql")
    for handle in ("h", "h", "s", "nope", "nope"):
        outcome = await resolve_capability_outcome(redis.get, handle, expected_audience=AUDIENCE_API)
        await record_resolution(redis, handle, *outcome)

    assert [e["handle_id"] for e in redis.events("first_use")] == [hid]
    # A real entry's refusal names it, with no digest; an unknown value is recorded once per window
    # by its digest (ADR-0042 dec 2, 8).
    assert [e["reason"] for e in redis.events("refused")] == ["wrong_audience", "no_entry"]
    named, unknown = redis.events("refused")
    assert named["company_id"] == cid and named["handle_id"] is not None and named["digest"] is None
    assert unknown["company_id"] is None and unknown["digest"] == audit.presented_digest("nope")


@pytest.mark.asyncio
async def test_a_handle_minted_before_the_record_resolves_without_a_first_use():
    from capability_auth import AUDIENCE_API, record_resolution, resolve_capability_outcome

    redis = FakeRedis()
    redis.strings["nbcap:old"] = _record(str(uuid.uuid4()))
    outcome = await resolve_capability_outcome(redis.get, "old", expected_audience=AUDIENCE_API)
    assert outcome[0] is not None
    await record_resolution(redis, "old", *outcome)
    assert redis.events() == []
