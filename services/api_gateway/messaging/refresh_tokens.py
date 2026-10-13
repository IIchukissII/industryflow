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
"""
import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Optional

from messaging.redis_client import redis_client
from config import get_settings

settings = get_settings()

_PREFIX = "refresh:"
_USER_PREFIX = "refresh-user:"


@dataclass(frozen=True)
class RefreshClaim:
    user_id: str
    credential: str


def _key(token: str) -> str:
    return f"{_PREFIX}{token}"


def _user_key(user_id: str) -> str:
    return f"{_USER_PREFIX}{user_id}"


def credential_fingerprint(hashed_password: str) -> str:
    """A fingerprint of the user's password hash: it changes whenever the password does."""
    return hashlib.sha256(hashed_password.encode()).hexdigest()


def _ttl() -> int:
    return settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600


async def create_refresh_token(user_id: str, hashed_password: str) -> str:
    """Issue a new opaque refresh token for a user, store it with a TTL and index it by user."""
    token = secrets.token_urlsafe(48)
    value = json.dumps({"uid": str(user_id), "cred": credential_fingerprint(hashed_password)})
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
        claim = RefreshClaim(user_id=str(data["uid"]), credential=str(data["cred"]))
    except (ValueError, KeyError, TypeError):
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
    try:
        user_id = str(json.loads(raw)["uid"])
    except (ValueError, KeyError, TypeError):
        user_id = raw
    await redis_client.redis.srem(_user_key(user_id), token)
    return user_id


async def revoke_all_refresh_tokens(user_id: str) -> int:
    """Revoke every refresh token of a user (end all sessions). Returns how many were live."""
    index = _user_key(str(user_id))
    tokens = await redis_client.redis.smembers(index)
    if not tokens:
        return 0
    live = await redis_client.redis.delete(*(_key(t.decode() if isinstance(t, bytes) else t) for t in tokens))
    # Remove exactly what was revoked: a token issued meanwhile stays indexed (and revocable).
    await redis_client.redis.srem(index, *tokens)
    return int(live)


async def end_all_sessions(user_id: str) -> None:
    """End everything a user is signed in with: every refresh token (platform session) and every
    notebook capability minted against them (ADR-0012 dec 6). Used on password change or reset,
    deactivation and deletion, as logout does for one session."""
    from capability_auth import revoke_user_capabilities

    await revoke_all_refresh_tokens(str(user_id))
    await revoke_user_capabilities(redis_client.redis, str(user_id))
