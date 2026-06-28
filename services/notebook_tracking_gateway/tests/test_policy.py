# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the tenant-scoping policy of the tracking gateway (ADR-0019)."""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import policy as p  # noqa: E402


class FakeAsyncStore:
    def __init__(self, mapping=None):
        self.mapping = mapping or {}

    async def get(self, key):
        return self.mapping.get(key)


def _rec(company_id, audience="tracking", user="ds"):
    return json.dumps({"user": user, "company_id": company_id, "audience": audience, "read_only": False})


@pytest.mark.asyncio
async def test_resolve_tracking_handle():
    cid = str(uuid.uuid4())
    store = FakeAsyncStore({f"{p._KEY_PREFIX}good": _rec(cid)})
    b = await p.resolve_tracking_binding(store.get, "good")
    assert b is not None and b.company_id == cid and b.user == "ds"


@pytest.mark.asyncio
async def test_non_tracking_audience_denied():
    cid = str(uuid.uuid4())
    store = FakeAsyncStore({f"{p._KEY_PREFIX}sql": _rec(cid, audience="sql")})
    assert await p.resolve_tracking_binding(store.get, "sql") is None


@pytest.mark.asyncio
async def test_absent_empty_malformed_denied():
    store = FakeAsyncStore({f"{p._KEY_PREFIX}bad": "{not json"})
    assert await p.resolve_tracking_binding(store.get, "missing") is None
    assert await p.resolve_tracking_binding(store.get, "") is None
    assert await p.resolve_tracking_binding(store.get, "bad") is None


def test_name_prefix_is_slash_free_artifact_prefix_is_path():
    cid = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"
    token = "tenant_0b2f503a_6e59_4f4f_b0cd_b82547a20cf2"
    # Names end in '.' (MLflow forbids '/' and ':' in registered-model names); artifact keys use '/'.
    assert p.tenant_prefix(cid) == token + "."
    assert "/" not in p.tenant_prefix(cid)
    assert p.artifact_prefix(cid) == token + "/"


def test_tenant_prefix_rejects_non_uuid():
    with pytest.raises(ValueError):
        p.tenant_prefix("'; DROP TABLE x; --")


def test_qualify_unqualify_roundtrip_and_idempotent():
    cid = str(uuid.uuid4())
    q = p.qualify_name(cid, "churn-model")
    assert q == p.tenant_prefix(cid) + "churn-model"
    assert p.qualify_name(cid, q) == q  # idempotent
    assert p.unqualify_name(cid, q) == "churn-model"


def test_cross_tenant_name_is_not_owned_and_unqualifies_to_none():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    other = p.qualify_name(b, "secret-model")
    assert p.owns_name(a, other) is False
    assert p.unqualify_name(a, other) is None  # never leak another tenant's name


def test_artifact_key_scoping():
    cid = str(uuid.uuid4())
    scoped = p.scope_artifact_key(cid, "models/run123/model.pkl")
    assert scoped == p.artifact_prefix(cid) + "models/run123/model.pkl"
    assert p.owns_artifact_key(cid, scoped) is True
    assert p.owns_artifact_key(cid, "tenant_other/models/x") is False
    assert p.scope_artifact_key(cid, "/" + scoped) == scoped  # idempotent + leading slash


def test_name_field_map_accessors():
    assert p.request_name_fields("experiments/create") == ["name"]
    assert p.response_name_paths("experiments/search") == ["experiments[].name"]
    assert p.request_name_fields("runs/log-metric") == []  # ids only, validated by lookup
