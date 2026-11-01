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

import asyncio
from typing import Optional

# A session that was ended by itself (its id is the access token's ``sid`` claim).
ENDED_SESSION_PREFIX = "session-ended:"
# When all of a user's sessions were last ended (epoch seconds).
USER_ENDED_PREFIX = "sessions-ended-at:"
# What a claimed refresh token was rotated into (JSON naming its user as ``uid``), kept for the
# token's lifetime so that presenting it again can be told apart: a concurrent refresh, or a reuse.
ROTATED_REFRESH_PREFIX = "refresh-rotated:"

# How far the clocks of the gateway's replicas may disagree: a token issued this long after an end of
# all the user's sessions still counts as issued before it.
CLOCK_SKEW_SECONDS = 2
# How long one read of the record may take before the answer counts as unknown.
READ_TIMEOUT_SECONDS = 2.0


def ended_session_key(session_id: str) -> str:
    return f"{ENDED_SESSION_PREFIX}{session_id}"


def user_ended_key(user_id) -> str:
    return f"{USER_ENDED_PREFIX}{user_id}"


def _text(value) -> str:
    return value.decode() if isinstance(value, (bytes, bytearray)) else str(value)


async def session_ended(store, user_id, session_id: Optional[str], issued_at,
                        timeout: float = READ_TIMEOUT_SECONDS) -> bool:
    """Whether the session an access token was issued in has since ended.

    True if that session was ended by itself, or all of the user's sessions were ended no earlier than
    ``CLOCK_SKEW_SECONDS`` before the token's issue time. A token with no issue time cannot be placed
    after such an end, so it counts as ended by it, as does an unreadable end time.

    A store error, or a read slower than ``timeout``, propagates: an unknown answer is not a yes.
    """
    return await asyncio.wait_for(_ended(store, user_id, session_id, issued_at), timeout)


async def _ended(store, user_id, session_id, issued_at) -> bool:
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
        return issued_at is None or float(issued_at) <= ended + CLOCK_SKEW_SECONDS
    except (TypeError, ValueError):
        return True
