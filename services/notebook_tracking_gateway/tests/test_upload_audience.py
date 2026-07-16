# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Audience non-interchangeability for the upload plane (ADR-0030 dec 3, ADR-0015 dec 3).

This is not a lock-on-a-door test. Both planes reach the same mediator, and the audience is the ONLY
fact separating two populations that are held to *different admission rules* — an uploaded artifact
faces the structural refusal, a kernel logging a model does not (ADR-0030 dec 4, deliberately). So a
handle that resolved for the wrong plane would not merely widen access: it would silently apply one
population's rules to the other. These tests are what keeps the two doors different doors.
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import policy  # noqa: E402

CID = str(uuid.uuid4())
OTHER = str(uuid.uuid4())


def _store(**records):
    async def aget(key):
        return records.get(key)
    return aget


def _rec(audience, company_id=CID, user="alice", **over):
    r = {"user": user, "company_id": company_id, "audience": audience}
    r.update(over)
    return json.dumps(r)


# --- each plane resolves its own -------------------------------------------------------------

@pytest.mark.asyncio
async def test_tracking_handle_resolves_on_the_tracking_plane():
    b = await policy.resolve_tracking_binding(_store(**{"nbcap:h": _rec("tracking")}), "h")
    assert b is not None and b.company_id == CID and b.audience == "tracking"


@pytest.mark.asyncio
async def test_upload_handle_resolves_on_the_upload_plane():
    b = await policy.resolve_upload_binding(_store(**{"nbcap:h": _rec("upload", read_only=False)}), "h")
    assert b is not None and b.company_id == CID and b.audience == "upload"


# --- ...and never the other's. Both directions. -------------------------------------------------

@pytest.mark.asyncio
async def test_a_kernels_tracking_handle_cannot_reach_the_upload_surface():
    """The larger half of ADR-0030 dec 3: if it could, every running kernel would be an uploader —
    and would face, or escape, admission rules that were never decided for it."""
    assert await policy.resolve_upload_binding(_store(**{"nbcap:h": _rec("tracking")}), "h") is None


@pytest.mark.asyncio
async def test_an_upload_handle_cannot_reach_the_kernels_tracking_plane():
    assert await policy.resolve_tracking_binding(_store(**{"nbcap:h": _rec("upload")}), "h") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("audience", ["api", "sql", "cold"])
async def test_no_other_plane_reaches_the_upload_surface(audience):
    assert await policy.resolve_upload_binding(_store(**{"nbcap:h": _rec(audience)}), "h") is None


# --- the audience is compared, never defaulted -------------------------------------------------

@pytest.mark.asyncio
async def test_a_record_with_no_audience_denies_everywhere():
    raw = json.dumps({"user": "alice", "company_id": CID})
    assert await policy.resolve_upload_binding(_store(**{"nbcap:h": raw}), "h") is None
    assert await policy.resolve_tracking_binding(_store(**{"nbcap:h": raw}), "h") is None


@pytest.mark.asyncio
async def test_read_only_true_does_not_make_an_upload_handle_a_tracking_handle():
    # read_only is a bound fact (ADR-0015 dec 1 rev 1), not the thing that picks a plane.
    raw = _rec("upload", read_only=True)
    assert await policy.resolve_tracking_binding(_store(**{"nbcap:h": raw}), "h") is None


# --- absent / revoked / malformed all deny (ADR-0015 dec 1-2) ----------------------------------

@pytest.mark.asyncio
async def test_absent_or_revoked_handle_denies():
    assert await policy.resolve_upload_binding(_store(), "gone") is None      # revocation = deletion
    assert await policy.resolve_upload_binding(_store(), "") is None


@pytest.mark.asyncio
async def test_malformed_records_deny():
    for raw in ("not json", "[]", '"a string"', json.dumps({"audience": "upload"})):  # last: no tenant
        assert await policy.resolve_upload_binding(_store(**{"nbcap:h": raw}), "h") is None


@pytest.mark.asyncio
async def test_bytes_from_the_store_are_decoded():
    b = await policy.resolve_upload_binding(_store(**{"nbcap:h": _rec("upload").encode()}), "h")
    assert b is not None and b.company_id == CID


# --- the tenant is the handle's, and only the handle's -----------------------------------------

@pytest.mark.asyncio
async def test_the_binding_carries_the_handles_own_tenant():
    b = await policy.resolve_upload_binding(_store(**{"nbcap:h": _rec("upload", company_id=OTHER)}), "h")
    assert b is not None and b.company_id == OTHER and b.company_id != CID
