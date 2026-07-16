# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit tests for demand-minting the upload capability (ADR-0030 dec 3, ADR-0015 dec 4 rev 1). Pure
rule, no store — mirroring the other capability-rule tests.

The property under test is not "a handle comes back". It is that the conditions ADR-0015 dec 4 rev 1
attaches to demand-minting are kept by construction: the tenant is the caller's verified one and is
never invented, the handle is opaque and short-lived, and the audience is one the spawner cannot
mint — because the audience is what holds uploads and kernels to different admission rules.
"""
import json
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import upload_capability as uc  # noqa: E402

CID = str(uuid.uuid4())


class FakeStore:
    def __init__(self):
        self.written = {}

    def set(self, key, value, ttl_seconds):
        self.written[key] = (value, ttl_seconds)


def _minted(store):
    (key, (raw, ttl)), = store.written.items()
    return key, json.loads(raw), ttl


# --- what a minted capability is bound to -----------------------------------------------------

def test_mint_binds_the_verified_user_and_exactly_one_tenant():
    s = FakeStore()
    handle = uc.mint(s.set, user="alice@example.com", company_id=CID)
    key, rec, _ = _minted(s)
    assert key == f"nbcap:{handle}"
    assert rec["user"] == "alice@example.com"
    assert rec["company_id"] == CID
    assert rec["audience"] == uc.AUDIENCE_UPLOAD


def test_the_upload_plane_is_not_read_only():
    # ADR-0015 dec 1 rev 1: a bound fact per handle. This plane writes one artifact; its boundary is
    # the tenant namespace the mediator enforces, not read-only-ness.
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID)
    _, rec, _ = _minted(s)
    assert rec["read_only"] is False


def test_the_audience_is_upload_and_nothing_else():
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID)
    _, rec, _ = _minted(s)
    assert rec["audience"] == "upload"
    assert rec["audience"] not in ("tracking", "api", "sql", "cold")


# --- the tenant is never invented (ADR-0003 dec 1) ---------------------------------------------

def test_mint_refuses_without_a_tenant():
    # A handle bound to nothing is not a weaker capability; it is a hole.
    s = FakeStore()
    with pytest.raises(ValueError):
        uc.mint(s.set, user="alice", company_id="")
    assert s.written == {}       # nothing was minted


def test_mint_refuses_without_a_verified_user():
    s = FakeStore()
    with pytest.raises(ValueError):
        uc.mint(s.set, user="", company_id=CID)
    assert s.written == {}


def test_mint_refuses_a_tenant_that_is_not_a_uuid():
    # The value becomes a namespace; a namespace that can be anything is not a boundary (ADR-0003).
    s = FakeStore()
    for bad in ("'; DROP", "tenant_x", "../../etc", "0b2f503a"):
        with pytest.raises(ValueError):
            uc.mint(s.set, user="alice", company_id=bad)
    assert s.written == {}


def test_the_tenant_is_canonicalised_not_echoed():
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID.upper())
    _, rec, _ = _minted(s)
    assert rec["company_id"] == CID          # canonical lowercase form, one spelling per tenant


# --- shape: opaque, high-entropy, short-lived --------------------------------------------------

def test_handles_are_opaque_and_never_repeat():
    s = FakeStore()
    handles = {uc.mint(s.set, user="alice", company_id=CID) for _ in range(200)}
    assert len(handles) == 200
    for h in handles:
        assert len(h) >= 40                  # 32 bytes, urlsafe-encoded
        assert CID not in h                  # the handle carries no facts — its authority is the entry


def test_it_expires_and_the_ttl_is_one_upload_not_one_session():
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID)
    _, _, ttl = _minted(s)
    assert ttl == uc.DEFAULT_TTL_SECONDS
    assert 0 < ttl <= 900


def test_mint_refuses_a_capability_that_would_not_expire():
    s = FakeStore()
    for bad_ttl in (0, -1):
        with pytest.raises(ValueError):
            uc.mint(s.set, user="alice", company_id=CID, ttl_seconds=bad_ttl)
    assert s.written == {}


# --- no session secret travels with it ---------------------------------------------------------

def test_the_record_holds_bound_facts_and_nothing_resembling_a_secret():
    # ADR-0015 dec 7: a reader of the store sees revocable, single-tenant handles and nothing whose
    # loss is worse than that.
    s = FakeStore()
    uc.mint(s.set, user="alice", company_id=CID)
    _, rec, _ = _minted(s)
    assert set(rec) == {"user", "company_id", "audience", "read_only"}
