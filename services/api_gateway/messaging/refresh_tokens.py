# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Server-side refresh-token store backed by Redis (ADR-0004 decision 2).

A refresh token is an opaque random string mapped to a user id with a TTL. Rotating a
token atomically deletes the old one and issues a new one (single-use), so a replayed or
already-used token is rejected. Revoking deletes it, ending the session. Because the store
is server-side, a session can be revoked before the access token would expire.
"""
import secrets
from typing import Optional, Tuple

from messaging.redis_client import redis_client
from config import get_settings

settings = get_settings()

_PREFIX = "refresh:"


def _key(token: str) -> str:
    return f"{_PREFIX}{token}"


async def create_refresh_token(user_id: str) -> str:
    """Issue a new opaque refresh token for a user and store it with a TTL."""
    token = secrets.token_urlsafe(48)
    ttl = settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    await redis_client.redis.set(_key(token), str(user_id), ex=ttl)
    return token


async def rotate_refresh_token(token: str) -> Optional[Tuple[str, str]]:
    """
    Validate and rotate a refresh token.

    Returns (user_id, new_token) on success, or None if the token is unknown, expired, or
    already used. The old token is deleted as part of rotation (single-use); the delete is
    used as the atomic check so a concurrent reuse cannot both succeed.
    """
    key = _key(token)
    user_id = await redis_client.redis.get(key)
    if user_id is None:
        return None
    # Single-use: the delete is the claim. If it returns 0 the token was already consumed.
    deleted = await redis_client.redis.delete(key)
    if not deleted:
        return None
    if isinstance(user_id, bytes):
        user_id = user_id.decode()
    new_token = await create_refresh_token(user_id)
    return user_id, new_token


async def revoke_refresh_token(token: str) -> None:
    """Revoke a refresh token (end the session). Idempotent."""
    await redis_client.redis.delete(_key(token))
