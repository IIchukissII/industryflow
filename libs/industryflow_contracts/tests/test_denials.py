# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The capability store's denials and its rebuild record (ADR-0015 dec 8, ADR-0033 dec 3)."""
import asyncio

from industryflow_contracts import denials, testing

COMPANY = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2"


def _refusals(entries, rebuilt=True):
    """What the async and the synchronous check answer for COMPANY on a store holding ``entries``."""
    async def both():
        store = await testing.async_redis(rebuilt=rebuilt)
        try:
            for key, value in entries.items():
                await store.set(key, value)
            held = {k: await store.get(k) for k in (denials.STORE_REBUILT_KEY, denials.suspended_key(COMPANY))}
            return await denials.tenant_refusal(store.get, COMPANY), denials.tenant_refusal_sync(held.get, COMPANY)
        finally:
            await store.aclose()

    return asyncio.run(both())


def test_a_rebuilt_store_without_a_marker_refuses_nothing():
    assert _refusals({}) == (None, None)


def test_a_marked_company_is_refused_as_suspended():
    assert _refusals({denials.suspended_key(COMPANY): "1"}) == (denials.TENANT_SUSPENDED,) * 2


def test_a_store_not_rebuilt_refuses_every_company_whatever_it_holds():
    """Recreated empty, the store cannot tell a company never suspended from one whose marker it lost."""
    assert _refusals({}, rebuilt=False) == (denials.STORE_NOT_REBUILT,) * 2
    assert _refusals({denials.suspended_key(COMPANY): "1"}, rebuilt=False) == (denials.STORE_NOT_REBUILT,) * 2


def test_an_unreadable_rebuild_record_is_no_rebuild():
    # The session check reads it the same way (sessions.session_ended): two answers on one key would grant.
    assert _refusals({denials.STORE_REBUILT_KEY: "garbage"}) == (denials.STORE_NOT_REBUILT,) * 2


def test_the_key_layout():
    assert denials.suspended_key(COMPANY) == f"nbcap-suspended:{COMPANY}"
    assert denials.STORE_REBUILT_KEY == "capability-store-rebuilt"
