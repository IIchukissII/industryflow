# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Server-side refresh-token store backed by Redis (ADR-0004 decision 2).

A refresh token is an opaque random string mapped to its user and a fingerprint of the user's
password hash, with a TTL. Refreshing claims the token (an atomic get-and-delete, so a replayed
or already-used token is rejected) and issues a new one. Revoking deletes it, ending the session.

Every token is also indexed under its user, so all of a user's sessions can be revoked at once:
on a password change or reset, on deactivation and on deletion (users.UserManager). The
password fingerprint covers what the index cannot see: a password set outside the gateway (the
admin CLI writes the database directly) no longer matches, so the old sessions cannot refresh.

A session has an id that its refresh tokens carry from rotation to rotation and that every access
token minted in it names (``sid``). Ending a session records that it ended, and ending all of a
user's sessions records when; both records outlive every access token issued before them, and that
is what every request re-checks (``session_ended``, ADR-0004 dec 2; the record's layout is the shared
contract ``industryflow_contracts.sessions``): an access token stops working when its session ends,
and a live socket closes within its re-check interval of it (dec 9).
"""
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Optional

from industryflow_contracts import sessions

from messaging.redis_client import redis_client
from config import get_settings

settings = get_settings()

_PREFIX = "refresh:"
_USER_PREFIX = "refresh-user:"


@dataclass(frozen=True)
class RefreshClaim:
    user_id: str
    credential: str
    # None for a token issued before sessions had ids: its rotation starts a new session.
    session_id: Optional[str] = None


def _key(token: str) -> str:
    return f"{_PREFIX}{token}"


def _user_key(user_id: str) -> str:
    return f"{_USER_PREFIX}{user_id}"


def credential_fingerprint(hashed_password: str) -> str:
    """A fingerprint of the user's password hash: it changes whenever the password does."""
    return hashlib.sha256(hashed_password.encode()).hexdigest()


def _ttl() -> int:
    return settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600


def _ended_ttl() -> int:
    """How long an ended-session record must live: past the expiry of any access token issued
    before it (one access-token lifetime, plus a minute of clock skew)."""
    return settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60 + 60


def new_session_id() -> str:
    return secrets.token_urlsafe(16)


async def create_refresh_token(user_id: str, hashed_password: str, session_id: Optional[str] = None) -> str:
    """Issue a new opaque refresh token for a user, store it with a TTL and index it by user.

    ``session_id`` is the session the token continues (a rotation); without one it starts a new
    session.
    """
    token = secrets.token_urlsafe(48)
    value = json.dumps({"uid": str(user_id), "cred": credential_fingerprint(hashed_password),
                        "sid": session_id or new_session_id()})
    async with redis_client.redis.pipeline(transaction=True) as pipe:
        pipe.set(_key(token), value, ex=_ttl())
        pipe.sadd(_user_key(str(user_id)), token)
        # The index lives as long as the newest token in it.
        pipe.expire(_user_key(str(user_id)), _ttl())
        await pipe.execute()
    return token


async def claim_refresh_token(token: str) -> Optional[RefreshClaim]:
    """
    Consume a refresh token (single use). Returns its claim, or None if the token is unknown,
    expired, already used, or in a format this version does not issue.

    GETDEL is the atomic claim: of two concurrent uses of one token, only one gets the value.
    """
    raw = await redis_client.redis.getdel(_key(token))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        data = json.loads(raw)
        sid = data.get("sid")
        claim = RefreshClaim(user_id=str(data["uid"]), credential=str(data["cred"]),
                             session_id=str(sid) if sid else None)
    except (ValueError, KeyError, TypeError, AttributeError):
        # Pre-fingerprint tokens held only a user id: they cannot prove the password is unchanged.
        return None
    await redis_client.redis.srem(_user_key(claim.user_id), token)
    return claim


async def revoke_refresh_token(token: str) -> Optional[str]:
    """Revoke a refresh token (end the session). Idempotent.

    Returns the user id the token belonged to, or None if it was unknown — so a logout can end
    what else belongs to that session's user (their notebook capabilities). A pre-fingerprint
    token (its value is the bare user id) is revoked and reported the same way.
    """
    raw = await redis_client.redis.getdel(_key(token))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    user_id, session_id = _owner(raw)
    if session_id:
        await _record_session_ended(session_id)
    await redis_client.redis.srem(_user_key(user_id), token)
    return user_id


def _owner(raw: str) -> tuple[str, Optional[str]]:
    """The user and session a stored token value names; a pre-fingerprint value is the bare user id."""
    try:
        data = json.loads(raw)
        sid = data.get("sid")
        return str(data["uid"]), (str(sid) if sid else None)
    except (ValueError, KeyError, TypeError, AttributeError):
        return raw, None


async def _record_session_ended(session_id: str) -> None:
    await redis_client.redis.set(sessions.ended_session_key(session_id), "1", ex=_ended_ttl())


async def revoke_all_refresh_tokens(user_id: str) -> int:
    """Revoke every refresh token of a user (end all sessions). Returns how many were live."""
    index = _user_key(str(user_id))
    tokens = await redis_client.redis.smembers(index)
    if not tokens:
        return 0
    live = 0
    for t in tokens:
        raw = await redis_client.redis.getdel(_key(t.decode() if isinstance(t, bytes) else t))
        if raw is None:
            continue
        live += 1
        _, session_id = _owner(raw.decode() if isinstance(raw, bytes) else raw)
        if session_id:
            await _record_session_ended(session_id)
    # Remove exactly what was revoked: a token issued meanwhile stays indexed (and revocable).
    await redis_client.redis.srem(index, *tokens)
    return live


async def end_all_sessions(user_id: str) -> None:
    """End everything a user is signed in with: every refresh token (platform session) and every
    notebook capability minted against them (ADR-0012 dec 6). Used on password change or reset,
    deactivation, deletion and the deactivation of the user's company, as logout does for one
    session. The time is recorded first, so an access token issued before this call can be told
    from one issued after it (``session_ended``)."""
    from capability_auth import revoke_user_capabilities

    await redis_client.redis.set(sessions.user_ended_key(user_id), repr(time.time()), ex=_ended_ttl())
    await revoke_all_refresh_tokens(str(user_id))
    await revoke_user_capabilities(redis_client.redis, str(user_id))


async def session_ended(user_id: str, session_id: Optional[str], issued_at: Optional[float]) -> bool:
    """Whether the session an access token was issued in has since ended (``sessions.session_ended``)."""
    return await sessions.session_ended(redis_client.redis, user_id, session_id, issued_at)
