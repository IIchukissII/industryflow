# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Redis a test runs against: a real server when one is given, the in-process fake otherwise.

The fake runs the store's commands and Lua scripts but has no client timeouts, no server limits and
no wire: a 5 s blocking read under redis-py's 5 s read timeout passed every test on the fake and
failed every idle pass on a real server (#501). So CI runs these tests against a real server
(``IF_TEST_REDIS_URL``), and ``IF_REQUIRE_REAL_REDIS=1`` makes the fake's absence of one a failure
rather than a quiet fallback. Every test that needs a store takes it from here
(tests/config_guards/test_tests_use_real_redis.py).

Imported by tests only; its dependencies are the tests' own (the ``dev`` extra) and load lazily.
"""
from __future__ import annotations

import os
from typing import Any

URL_ENV = "IF_TEST_REDIS_URL"
REQUIRE_ENV = "IF_REQUIRE_REAL_REDIS"


def real_redis_url():
    """The real server's URL, or None; with ``IF_REQUIRE_REAL_REDIS=1`` and none given, an error."""
    url = os.environ.get(URL_ENV) or None
    if url is None and os.environ.get(REQUIRE_ENV) == "1":
        raise RuntimeError(f"{REQUIRE_ENV}=1 but {URL_ENV} is not set: these tests would run on the fake")
    return url


def sync_redis(**kwargs: Any):
    """A synchronous client on an empty database: the real server's, or a fresh fake."""
    url = real_redis_url()
    if url is None:
        import fakeredis
        return fakeredis.FakeRedis(**kwargs)
    import redis
    client = redis.Redis.from_url(url, **kwargs)
    client.flushdb()
    return client


async def async_redis(**kwargs: Any):
    """An asyncio client on an empty database: the real server's, or a fresh fake."""
    url = real_redis_url()
    if url is None:
        import fakeredis
        return fakeredis.FakeAsyncRedis(**kwargs)
    import redis.asyncio as aioredis
    client = aioredis.from_url(url, **kwargs)
    await client.flushdb()
    return client


def redis_pair(sync_kwargs=None, async_kwargs=None):
    """A synchronous and an asyncio client on one empty database, for a test that drives code through
    one and reads the store back through the other."""
    sync_kwargs, async_kwargs = sync_kwargs or {}, async_kwargs or {}
    url = real_redis_url()
    if url is None:
        import fakeredis
        server = fakeredis.FakeServer()
        return (fakeredis.FakeRedis(server=server, **sync_kwargs),
                fakeredis.FakeAsyncRedis(server=server, **async_kwargs))
    import redis
    import redis.asyncio as aioredis
    sync = redis.Redis.from_url(url, **sync_kwargs)
    sync.flushdb()
    return sync, aioredis.from_url(url, **async_kwargs)


def is_real(client: Any) -> bool:
    return not type(client).__module__.startswith("fakeredis")
