# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Verifier-side capability resolution tests (ADR-0015), on industryflow_contracts.testing's store
(a real Redis in CI)."""
import json
import uuid

import pytest
from industryflow_contracts import denials

from industryflow_contracts import capability_audit as audit
from industryflow_contracts import testing


async def _store(entries=(), **kwargs):
    """The capability store holding ``entries``, connected as messaging.redis_client connects unless
    ``kwargs`` say otherwise."""
    redis = await testing.async_redis(**(kwargs or {"encoding": "utf-8", "decode_responses": True}))
    for key, value in dict(entries).items():
        await redis.set(key, value)
    return redis


async def _keys(redis, kind):
    """The store's keys of one Redis type, not counting the capability-use record's own or the store's
    rebuild record."""
    return {k for k in await redis.keys("*")
            if not k.startswith("nbcap-audit") and k != denials.STORE_REBUILT_KEY and await redis.type(k) == kind}


def _record(company_id, audience="api", user="u", read_only=True):
    return json.dumps(
        {"user": user, "company_id": company_id, "audience": audience, "read_only": read_only}
    )


@pytest.mark.asyncio
async def test_resolve_roundtrip():
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    cid = str(uuid.uuid4())
    store = await _store({f"{_KEY_PREFIX}h1": _record(cid)})
    binding = await resolve_capability(store.get, "h1", expected_audience=AUDIENCE_API)
    assert binding is not None
    assert binding.company_id == cid
    assert binding.audience == AUDIENCE_API
    assert binding.read_only is True


@pytest.mark.asyncio
async def test_resolve_accepts_bytes_value():
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    cid = str(uuid.uuid4())
    store = await _store({f"{_KEY_PREFIX}h1": _record(cid)}, decode_responses=False)  # bytes back
    binding = await resolve_capability(store.get, "h1", expected_audience=AUDIENCE_API)
    assert binding is not None and binding.company_id == cid


@pytest.mark.asyncio
async def test_wrong_audience_denies():
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    store = await _store({f"{_KEY_PREFIX}h1": _record(str(uuid.uuid4()), audience="sql")})
    assert await resolve_capability(store.get, "h1", expected_audience=AUDIENCE_API) is None


@pytest.mark.asyncio
async def test_absent_empty_and_malformed_deny():
    from capability_auth import resolve_capability, AUDIENCE_API, _KEY_PREFIX

    store = await _store({f"{_KEY_PREFIX}bad": "not-json"})
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

    store = await _store({f"{_KEY_PREFIX}h": raw})
    assert await resolve_capability(store.get, "h", expected_audience=AUDIENCE_API) is None


# --- platform logout revokes the user's notebook capabilities (ADR-0012 dec 6) --------------------

async def _seed(redis, user, handles):
    for h in handles:
        await redis.set(f"nbcap:{h}", _record(str(uuid.uuid4())))
    await redis.hset(f"nbcap-user:{user}", mapping={h: "" for h in handles})


@pytest.mark.asyncio
async def test_revoking_a_user_revokes_exactly_their_handles():
    from capability_auth import revoke_user_capabilities

    redis = await _store()
    await _seed(redis, "alice", ["a1", "a2"])
    await _seed(redis, "bob", ["b1"])

    assert await revoke_user_capabilities(redis, "alice") == 2
    assert await _keys(redis, "string") == {"nbcap:b1"}
    assert await _keys(redis, "hash") == {"nbcap-user:bob"}


@pytest.mark.asyncio
async def test_logout_revokes_the_users_notebook_capabilities(monkeypatch):
    from types import SimpleNamespace

    from messaging.redis_client import redis_client
    from routers import auth_tokens

    redis = await _store()
    monkeypatch.setattr(redis_client, "redis", redis)
    await redis.set("refresh:tok", "alice")
    await _seed(redis, "alice", ["a1", "a2"])
    await _seed(redis, "bob", ["b1"])

    request = SimpleNamespace(cookies={auth_tokens.REFRESH_COOKIE: "tok"}, headers={})
    from fastapi import Response

    await auth_tokens.logout(request, Response(), None)

    assert not await redis.exists("refresh:tok")
    assert await _keys(redis, "string") == {"nbcap:b1"}  # alice's handles are gone, bob's are not


@pytest.mark.asyncio
async def test_logout_with_an_unknown_refresh_token_revokes_nothing(monkeypatch):
    from types import SimpleNamespace

    from fastapi import Response
    from messaging.redis_client import redis_client
    from routers import auth_tokens

    redis = await _store()
    monkeypatch.setattr(redis_client, "redis", redis)
    await _seed(redis, "alice", ["a1"])

    await auth_tokens.logout(SimpleNamespace(cookies={auth_tokens.REFRESH_COOKIE: "stale"}, headers={}), Response(), None)
    assert await _keys(redis, "string") == {"nbcap:a1"}


@pytest.mark.asyncio
async def test_revoking_a_user_also_revokes_their_upload_handles():
    # The model-upload minter indexes each handle it demand-mints under its user, so ending the
    # user's sessions ends those too (ADR-0004 dec 2).
    from capability_auth import revoke_user_capabilities

    redis = await _store()
    await _seed(redis, "alice", ["a1"])
    await redis.set("nbcap:u1", _record(str(uuid.uuid4()), audience="upload"))
    await redis.sadd("nbcap-upload-user:alice", "u1")
    await redis.set("nbcap:u2", _record(str(uuid.uuid4()), audience="upload"))
    await redis.sadd("nbcap-upload-user:bob", "u2")

    assert await revoke_user_capabilities(redis, "alice") == 2
    assert await _keys(redis, "string") == {"nbcap:u2"}
    assert await _keys(redis, "set") == {"nbcap-upload-user:bob"}


# --- the capability-use record (ADR-0042) ------------------------------------------------------

def _audited(company_id, handle_id, audience="api", user="alice"):
    return json.dumps({"user": user, "company_id": company_id, "audience": audience, "read_only": True,
                       "handle_id": handle_id})


@pytest.mark.asyncio
async def test_a_revocation_is_recorded_with_the_handles_identifier_never_the_handle():
    from capability_auth import revoke_user_capabilities

    redis = await _store()
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    await redis.set("nbcap:secret-a1", _audited(cid, hid))
    await redis.hset("nbcap-user:alice", mapping={"secret-a1": "", "expired-a2": ""})

    assert await revoke_user_capabilities(redis, "alice") == 1
    [event] = await testing.audit_events(redis, "revoked")
    assert (event["handle_id"], event["company_id"], event["reason"]) == (hid, cid, "user_signed_out")
    stream = await testing.audit_stream(redis)
    assert not any("secret-a1" in v for e in stream for v in e.values())
    assert len(stream) == 1  # the handle that had already expired was revoked by no one


@pytest.mark.asyncio
async def test_first_use_is_recorded_once_and_an_unknown_value_once_per_window():
    from capability_auth import AUDIENCE_API, record_resolution, resolve_capability_outcome

    redis = await _store()
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    await redis.set("nbcap:h", _audited(cid, hid))
    await redis.set("nbcap:s", _audited(cid, str(uuid.uuid4()), audience="sql"))
    for handle in ("h", "h", "s", "nope", "nope"):
        outcome = await resolve_capability_outcome(redis.get, handle, expected_audience=AUDIENCE_API)
        await record_resolution(redis, handle, *outcome)

    assert [e["handle_id"] for e in await testing.audit_events(redis, "first_use")] == [hid]
    # A real entry's refusal names it, with no digest; an unknown value is recorded once per window
    # by its digest (ADR-0042 dec 2, 8).
    assert [e["reason"] for e in await testing.audit_events(redis, "refused")] == ["wrong_audience", "no_entry"]
    named, unknown = await testing.audit_events(redis, "refused")
    assert named["company_id"] == cid and named["handle_id"] is not None and named["digest"] is None
    assert unknown["company_id"] is None and unknown["digest"] == audit.presented_digest("nope")


@pytest.mark.asyncio
async def test_a_kernel_looping_its_own_handle_at_the_wrong_plane_is_one_record_and_a_count():
    # A kernel holds real handles of every audience; sending its SQL handle here at request rate is
    # recorded once per window, and the next record carries the count (ADR-0042 dec 8).
    from capability_auth import AUDIENCE_API, record_resolution, resolve_capability_outcome

    redis = await _store()
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    await redis.set("nbcap:s", _audited(cid, hid, audience="sql"))

    async def present():
        outcome = await resolve_capability_outcome(redis.get, "s", expected_audience=AUDIENCE_API)
        await record_resolution(redis, "s", *outcome, source="10.0.0.9")

    for _ in range(200):
        await present()
    assert len(await testing.audit_events(redis, "refused")) == 1
    await testing.pass_audit_windows(redis)  # the window passes
    await present()
    assert [(e["handle_id"], e["suppressed"]) for e in await testing.audit_events(redis, "refused")] == [(hid, None), (hid, "199")]


@pytest.mark.asyncio
async def test_a_handle_minted_before_the_record_resolves_without_a_first_use():
    from capability_auth import AUDIENCE_API, record_resolution, resolve_capability_outcome

    redis = await _store()
    await redis.set("nbcap:old", _record(str(uuid.uuid4())))
    outcome = await resolve_capability_outcome(redis.get, "old", expected_audience=AUDIENCE_API)
    assert outcome[0] is not None
    await record_resolution(redis, "old", *outcome)
    assert await testing.audit_events(redis) == []
