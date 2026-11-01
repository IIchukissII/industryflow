# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ended-session record (ADR-0004 dec 2): which access tokens no longer stand for a session."""
import asyncio

import pytest

from industryflow_contracts import sessions, testing

USER = "7d0c1f4e-2b8a-4c39-9f61-0a5e3b2d4c11"


def _ended(records, checks):
    """Write ``records`` to an empty store, then answer each (user, session, issued_at) in ``checks``."""
    async def run():
        store = await testing.async_redis()
        try:
            for key, value in records.items():
                await store.set(key, value)
            return [await sessions.session_ended(store, *check) for check in checks]
        finally:
            await store.aclose()

    return asyncio.run(run())


def test_a_session_with_no_record_has_not_ended():
    assert _ended({}, [(USER, "s", 100), (USER, None, None)]) == [False, False]


def test_a_session_ended_by_itself_is_ended_whatever_its_issue_time():
    records = {sessions.ended_session_key("s"): "1"}
    assert _ended(records, [(USER, "s", 10**12), (USER, "other", 10**12)]) == [True, False]


@pytest.mark.parametrize("issued_at, ended", [(99, True), (100, True), (100.0, True), (101, False),
                                              (None, True), ("not a time", True)])
def test_ending_all_sessions_ends_the_tokens_issued_at_or_before_it(issued_at, ended):
    records = {sessions.user_ended_key(USER): "100.4"}
    assert _ended(records, [(USER, "s", issued_at), ("someone-else", "s", issued_at)]) == [ended, False]


def test_an_unreadable_end_time_counts_as_ended():
    assert _ended({sessions.user_ended_key(USER): "garbage"}, [(USER, "s", 10**12)]) == [True]


def test_a_store_error_propagates():
    class Down:
        async def get(self, key):
            raise ConnectionError("down")

    with pytest.raises(ConnectionError):
        asyncio.run(sessions.session_ended(Down(), USER, "s", 1))


def test_the_key_layout():
    assert sessions.ended_session_key("abc") == "session-ended:abc"
    assert sessions.user_ended_key(USER) == f"sessions-ended-at:{USER}"
