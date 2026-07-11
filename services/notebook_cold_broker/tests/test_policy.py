# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pure policy tests for the cold-store broker (ADR-0025 dec 5, read side)."""
import asyncio
import json
import uuid

import pytest

import policy  # noqa: E402

CID = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"


def _get(mapping):
    async def aget(key):
        return mapping.get(key)
    return aget


def _resolve(mapping, handle):
    return asyncio.run(policy.resolve_cold_binding(_get(mapping), handle))


def test_prefix_matches_exporter_hyphenated_uuid():
    # MUST equal cold_export/naming.company_id_to_prefix + "/", i.e. hyphenated — NOT the
    # underscore form the SQL/tracking planes use. A mismatch would read an empty prefix.
    assert policy.tenant_prefix(CID) == f"tenant_{CID}/"
    assert "_" not in policy.tenant_prefix(CID)[len("tenant_"):].rstrip("/")  # no underscores in the uuid part


def test_prefix_validates_uuid():
    with pytest.raises(ValueError):
        policy.tenant_prefix("not-a-uuid")


def test_resolve_valid_cold_handle():
    rec = {"user": "u", "company_id": CID, "audience": "cold", "read_only": True}
    b = _resolve({"nbcap:h": json.dumps(rec)}, "h")
    assert b is not None and b.company_id == CID and b.user == "u"


def test_resolve_denies_wrong_audience():
    rec = {"user": "u", "company_id": CID, "audience": "sql"}
    assert _resolve({"nbcap:h": json.dumps(rec)}, "h") is None


def test_resolve_denies_absent_and_malformed():
    assert _resolve({}, "missing") is None
    assert _resolve({"nbcap:h": "not json"}, "h") is None
    assert _resolve({}, "") is None


def test_scope_key_forces_prefix_idempotent():
    assert policy.scope_key(CID, "year=2026/x.parquet") == f"tenant_{CID}/year=2026/x.parquet"
    # already scoped -> unchanged
    assert policy.scope_key(CID, f"tenant_{CID}/a") == f"tenant_{CID}/a"
    # leading slash tolerated
    assert policy.scope_key(CID, "/y") == f"tenant_{CID}/y"


def test_scope_key_rejects_dotdot():
    with pytest.raises(ValueError):
        policy.scope_key(CID, "../tenant_other/secret.parquet")


def test_cross_tenant_key_reprefixed_not_reachable():
    other = str(uuid.uuid4())
    # A path that names another tenant's prefix is treated as a relative path and prepended, so it
    # lands under the CALLER's prefix — never the other tenant's.
    scoped = policy.scope_key(CID, f"tenant_{other}/x")
    assert scoped == f"tenant_{CID}/tenant_{other}/x"
    assert policy.owns_key(CID, scoped)
    assert not policy.owns_key(other, scoped)


def test_owns_key_rejects_other_tenant():
    other = str(uuid.uuid4())
    assert not policy.owns_key(CID, f"tenant_{other}/x")
    assert policy.owns_key(CID, f"tenant_{CID}/x")
