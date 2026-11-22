# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The shared count of tenant quotas (ADR-0049 dec 4): a store apart from the capability store, holding
expiring counters only. The gateway reads the refusal counts from it."""
from typing import Optional

import redis.asyncio as aioredis

from config import get_settings

_client: Optional[aioredis.Redis] = None


def quota_store() -> aioredis.Redis:
    """The client, built on first use; it connects lazily."""
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().QUOTA_REDIS_URL, encoding="utf-8", decode_responses=True,
                                    socket_connect_timeout=2, socket_timeout=2)
    return _client
