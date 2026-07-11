# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for capability mint/resolve/revoke (ADR-0015), against an in-memory fake store."""
import os
import sys
import uuid

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import capabilities as cap  # noqa: E402


class FakeStore:
    """In-memory CapabilityStore; ignores TTL (expiry is the store's job in production)."""

    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    def set(self, key, value, ttl_seconds):
        self.data[key] = value
        self.ttls[key] = ttl_seconds

    def get(self, key):
        return self.data.get(key)

    def delete(self, key):
        self.data.pop(key, None)
        self.ttls.pop(key, None)


def _mint(store, audience=cap.AUDIENCE_API, company_id=None):
    return cap.mint(
        store,
        user="alice",
        company_id=company_id or str(uuid.uuid4()),
        audience=audience,
        ttl_seconds=300,
    )


def test_mint_then_resolve_roundtrip():
    store = FakeStore()
    cid = str(uuid.uuid4())
    handle = _mint(store, company_id=cid)

    binding = cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API)
    assert binding is not None
    assert binding.user == "alice"
    assert binding.company_id == cid
    assert binding.audience == cap.AUDIENCE_API
    assert binding.read_only is True


def test_handle_is_opaque_and_high_entropy():
    store = FakeStore()
    h1, h2 = _mint(store), _mint(store)
    assert h1 != h2
    assert len(h1) >= 32
    # The handle reveals nothing about the binding.
    assert "alice" not in h1


def test_revoke_denies_next_use():
    store = FakeStore()
    handle = _mint(store)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is not None
    cap.revoke(store, handle)
    assert cap.resolve(store, handle, expected_audience=cap.AUDIENCE_API) is None


def test_expiry_or_absence_denies():
    store = FakeStore()
    assert cap.resolve(store, "never-minted", expected_audience=cap.AUDIENCE_API) is None
    assert cap.resolve(store, "", expected_audience=cap.AUDIENCE_API) is None


def test_audiences_are_not_interchangeable():
    store = FakeStore()
    api_handle = _mint(store, audience=cap.AUDIENCE_API)
    sql_handle = _mint(store, audience=cap.AUDIENCE_SQL)

    # Each resolves only for its own audience (ADR-0015 dec 3).
    assert cap.resolve(store, api_handle, expected_audience=cap.AUDIENCE_API) is not None
    assert cap.resolve(store, api_handle, expected_audience=cap.AUDIENCE_SQL) is None
    assert cap.resolve(store, sql_handle, expected_audience=cap.AUDIENCE_SQL) is not None
    assert cap.resolve(store, sql_handle, expected_audience=cap.AUDIENCE_API) is None


def test_mint_rejects_unknown_audience():
    with pytest.raises(ValueError):
        cap.mint(FakeStore(), user="a", company_id=str(uuid.uuid4()), audience="other", ttl_seconds=300)


@pytest.mark.parametrize("user,company", [("", "c"), ("u", "")])
def test_mint_requires_user_and_tenant(user, company):
    with pytest.raises(ValueError):
        cap.mint(FakeStore(), user=user, company_id=company, audience=cap.AUDIENCE_API, ttl_seconds=300)


def test_store_holds_no_database_secret():
    # The stored record carries only binding facts, never a password/secret (ADR-0015 dec 7).
    store = FakeStore()
    _mint(store)
    blob = " ".join(store.data.values()).lower()
    assert "password" not in blob and "secret" not in blob


def test_tracking_audience_is_read_write_and_distinct():
    store = FakeStore()
    h = _mint(store, audience=cap.AUDIENCE_TRACKING)
    # Tracking resolves only for tracking, and (unlike api/sql) is read-write within the tenant
    # namespace the gateway enforces (ADR-0019).
    b = cap.resolve(store, h, expected_audience=cap.AUDIENCE_TRACKING)
    assert b is not None and b.audience == cap.AUDIENCE_TRACKING
    assert b.read_only is False
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_SQL) is None
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_API) is None


def test_cold_audience_is_read_only_and_distinct():
    store = FakeStore()
    h = _mint(store, audience=cap.AUDIENCE_COLD)
    # Cold resolves only for cold, and is read-only (training reads history, writes nothing —
    # ADR-0025 dec 5).
    b = cap.resolve(store, h, expected_audience=cap.AUDIENCE_COLD)
    assert b is not None and b.audience == cap.AUDIENCE_COLD
    assert b.read_only is True
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_TRACKING) is None
    assert cap.resolve(store, h, expected_audience=cap.AUDIENCE_SQL) is None


def test_unknown_audience_rejected():
    store = FakeStore()
    import pytest
    with pytest.raises(ValueError):
        cap.mint(store, user="u", company_id="c", audience="mlflow-direct", ttl_seconds=60)
