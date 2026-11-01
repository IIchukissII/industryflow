# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The ended-session record (ADR-0004 dec 2): which access tokens no longer stand for a session.

The gateway writes it in the shared capability store when a session ends (logout) and when all of a
user's sessions end (a password change or reset, deactivation, deletion, the company's suspension).
Every verifier of an access token reads it on every request, so an access token stops working when
its session ends rather than when it expires. Each record outlives every access token issued before
it.

Standard library only: this is imported by every service image. ``store`` is any asyncio Redis client.
"""
from __future__ import annotations

from typing import Optional

# A session that was ended by itself (its id is the access token's ``sid`` claim).
ENDED_SESSION_PREFIX = "session-ended:"
# When all of a user's sessions were last ended (epoch seconds).
USER_ENDED_PREFIX = "sessions-ended-at:"


def ended_session_key(session_id: str) -> str:
    return f"{ENDED_SESSION_PREFIX}{session_id}"


def user_ended_key(user_id) -> str:
    return f"{USER_ENDED_PREFIX}{user_id}"


def _text(value) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


async def session_ended(store, user_id, session_id: Optional[str], issued_at) -> bool:
    """Whether the session an access token was issued in has since ended.

    True if that session was ended by itself, or all of the user's sessions were ended at or after the
    token's issue time. A token with no issue time cannot be placed after such an end, so it counts as
    ended by it, as does an unreadable end time. Whole seconds round towards ended: a token issued in
    the same second as the end is treated as issued before it, and its holder refreshes.

    A store error propagates: an unknown answer is not a yes.
    """
    if session_id and await store.get(ended_session_key(str(session_id))) is not None:
        return True
    ended_at = await store.get(user_ended_key(str(user_id)))
    if ended_at is None:
        return False
    try:
        ended = float(_text(ended_at))
    except ValueError:
        return True
    try:
        return issued_at is None or float(issued_at) <= ended
    except (TypeError, ValueError):
        return True
