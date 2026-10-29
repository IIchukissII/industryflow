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


@pytest.mark.parametrize("key", ["../x", "a/../../x", "a/./b", "a//b", "a\\..\\b"])
def test_an_artifact_key_that_is_not_plain_is_refused(key):
    cid = str(uuid.uuid4())
    with pytest.raises(ValueError):
        p.scope_artifact_key(cid, key)
    assert p.owns_artifact_key(cid, p.artifact_prefix(cid) + key) is False


def test_an_encoded_artifact_uri_key_is_not_owned():
    cid = str(uuid.uuid4())
    assert p.owns_artifact_key(cid, p.artifact_prefix(cid) + "%2e%2e/x") is False
    assert p.scope_artifact_key(cid, "dir/") == p.artifact_prefix(cid) + "dir/"


@pytest.mark.parametrize("audience, resolve", [
    ("tracking", p.resolve_tracking_binding), ("upload", p.resolve_upload_binding)])
@pytest.mark.asyncio
async def test_a_suspended_tenants_handle_is_denied(audience, resolve):
    # ADR-0033 dec 3: while the platform marks the company suspended, none of its handles resolve.
    cid = str(uuid.uuid4())
    store = FakeAsyncStore({"nbcap:h": _rec(cid, audience=audience)})
    assert await resolve(store.get, "h") is not None
    store.mapping[f"nbcap-suspended:{cid}"] = "1"
    assert await resolve(store.get, "h") is None


@pytest.mark.asyncio
async def test_a_binding_carries_the_entrys_handle_id_and_tolerates_none():
    """The non-secret identifier the record names a handle by (ADR-0042 dec 2); entries minted before
    it, or carrying something that is not one, resolve with none."""
    cid, hid = str(uuid.uuid4()), str(uuid.uuid4())
    with_id = json.dumps({"user": "ds", "company_id": cid, "audience": "tracking", "handle_id": hid})
    bad_id = json.dumps({"user": "ds", "company_id": cid, "audience": "tracking", "handle_id": "x"})
    store = FakeAsyncStore({f"{p._KEY_PREFIX}a": with_id, f"{p._KEY_PREFIX}b": bad_id,
                            f"{p._KEY_PREFIX}c": _rec(cid)})
    assert (await p.resolve_tracking_binding(store.get, "a")).handle_id == hid
    assert (await p.resolve_tracking_binding(store.get, "b")).handle_id is None
    assert (await p.resolve_tracking_binding(store.get, "c")).handle_id is None


@pytest.mark.asyncio
async def test_a_refusal_says_why():
    cid = str(uuid.uuid4())
    store = FakeAsyncStore({f"{p._KEY_PREFIX}up": _rec(cid, audience="upload"),
                            f"{p._KEY_PREFIX}bad": "{not json",
                            f"{p._KEY_PREFIX}tr": _rec(cid), f"nbcap-suspended:{cid}": "1"})
    reasons = {h: (await p.resolve_tracking_outcome(store.get, h))[1] for h in ("", "missing", "bad", "up", "tr")}
    assert reasons == {"": "no_handle", "missing": "no_entry", "bad": "malformed_entry",
                       "up": "wrong_audience", "tr": "tenant_suspended"}
