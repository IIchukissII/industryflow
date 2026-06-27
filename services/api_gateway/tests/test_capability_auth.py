# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Verifier-side capability resolution tests (ADR-0015), with a fake async store."""
import json
import uuid

import pytest


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
