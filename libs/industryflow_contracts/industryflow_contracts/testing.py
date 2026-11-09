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

Each store starts as the platform's runs: rebuilt (``denials.STORE_REBUILT_KEY``, at time 0, so it ends
no session), unless a test asks for one that is not (``rebuilt=False``).

Imported by tests only; its dependencies are the tests' own (the ``dev`` extra) and load lazily.
"""
from __future__ import annotations

import os
from typing import Any

from .denials import STORE_REBUILT_KEY

URL_ENV = "IF_TEST_REDIS_URL"
REQUIRE_ENV = "IF_REQUIRE_REAL_REDIS"
# Every test empties its database first. A server becomes a test server only while that database is
# already empty; this key, in a database of its own, says it has. A database that holds keys on a
# server without it is refused rather than emptied: the URL names a store someone uses.
MARKER_DB = 15
MARKER_KEY = "industryflow:test-server"


def real_redis_url():
    """The real server's URL, or None; with ``IF_REQUIRE_REAL_REDIS=1`` and none given, an error."""
    url = os.environ.get(URL_ENV) or None
    if url is None and os.environ.get(REQUIRE_ENV) == "1":
        raise RuntimeError(f"{REQUIRE_ENV}=1 but {URL_ENV} is not set: these tests would run on the fake")
    return url


def _emptied(url: str) -> None:
    """Empty the database ``url`` names, or refuse to (``RuntimeError``) on a server not marked as a
    test server whose database holds keys."""
    import redis
    with redis.Redis.from_url(url) as target:
        pool = target.connection_pool
        if pool.connection_kwargs.get("db", 0) == MARKER_DB:
            raise RuntimeError(f"{URL_ENV} names database {MARKER_DB}, which holds the test-server marker")
        # The same server and credentials, the marker's database (a URL's path outranks ``db=``).
        marker_pool = redis.ConnectionPool(connection_class=pool.connection_class,
                                           **{**pool.connection_kwargs, "db": MARKER_DB})
        with redis.Redis(connection_pool=marker_pool) as marker:
            if not marker.exists(MARKER_KEY):
                held = target.dbsize()
                if held:
                    raise RuntimeError(
                        f"refusing to empty {url}: it holds {held} keys and the server is not a test server "
                        f"(no {MARKER_KEY} in database {MARKER_DB}); {URL_ENV} must name a throwaway server")
                marker.set(MARKER_KEY, "1")
        target.flushdb()


def _start(sync_client, rebuilt: bool) -> None:
    if rebuilt:
        sync_client.set(STORE_REBUILT_KEY, "0")


def sync_redis(rebuilt: bool = True, **kwargs: Any):
    """A synchronous client on an empty database: the real server's, or a fresh fake."""
    url = real_redis_url()
    if url is None:
        import fakeredis
        client = fakeredis.FakeRedis(**kwargs)
    else:
        import redis
        _emptied(url)
        client = redis.Redis.from_url(url, **kwargs)
    _start(client, rebuilt)
    return client


async def async_redis(rebuilt: bool = True, **kwargs: Any):
    """An asyncio client on an empty database: the real server's, or a fresh fake."""
    url = real_redis_url()
    if url is None:
        import fakeredis
        server = fakeredis.FakeServer()
        _start(fakeredis.FakeRedis(server=server), rebuilt)
        return fakeredis.FakeAsyncRedis(server=server, **kwargs)
    import redis
    import redis.asyncio as aioredis
    _emptied(url)
    with redis.Redis.from_url(url) as seed:
        _start(seed, rebuilt)
    return aioredis.from_url(url, **kwargs)


def redis_pair(sync_kwargs=None, async_kwargs=None, rebuilt: bool = True):
    """A synchronous and an asyncio client on one empty database, for a test that drives code through
    one and reads the store back through the other."""
    sync_kwargs, async_kwargs = sync_kwargs or {}, async_kwargs or {}
    url = real_redis_url()
    if url is None:
        import fakeredis
        server = fakeredis.FakeServer()
        pair = (fakeredis.FakeRedis(server=server, **sync_kwargs),
                fakeredis.FakeAsyncRedis(server=server, **async_kwargs))
    else:
        import redis
        import redis.asyncio as aioredis
        _emptied(url)
        pair = redis.Redis.from_url(url, **sync_kwargs), aioredis.from_url(url, **async_kwargs)
    _start(pair[0], rebuilt)
    return pair


class AnyLoopRedis:
    """An asyncio client per event loop on one empty database, for a test that reaches the store from
    more than one loop: starlette's TestClient runs the app on a loop of its own per request, and a
    redis-py asyncio connection is bound to the loop that opened it. Each loop gets the client
    ``async_redis`` would give it; a test reads and writes through this object from any loop, or
    through ``sync()`` outside one. ``entries`` (string keys to values) are written first."""

    def __init__(self, entries: Any = None, rebuilt: bool = True, **kwargs: Any):
        self._kwargs, self._clients = kwargs, {}
        self._url = real_redis_url()
        if self._url is None:
            import fakeredis
            self._server = fakeredis.FakeServer()
        if self._url is not None:
            _emptied(self._url)
        with self.sync() as client:
            _start(client, rebuilt)
            for key, value in dict(entries or {}).items():
                client.set(key, value)

    def sync(self, **kwargs: Any):
        """A synchronous client on the same database."""
        if self._url is None:
            import fakeredis
            return fakeredis.FakeRedis(server=self._server, **kwargs)
        import redis
        return redis.Redis.from_url(self._url, **kwargs)

    def loop_client(self):
        """The asyncio client of the running loop."""
        import asyncio
        loop = asyncio.get_running_loop()
        client = self._clients.get(loop)
        if client is None:
            if self._url is None:
                import fakeredis
                client = fakeredis.FakeAsyncRedis(server=self._server, **self._kwargs)
            else:
                import redis.asyncio as aioredis
                client = aioredis.from_url(self._url, **self._kwargs)
            self._clients[loop] = client
        return client

    def __getattr__(self, name: str):
        return getattr(self.loop_client(), name)


def is_real(client: Any) -> bool:
    if isinstance(client, AnyLoopRedis):
        return client._url is not None
    return not type(client).__module__.startswith("fakeredis")


# --- the capability-use record (industryflow_contracts.capability_audit), read back from the store ---

def _text(value: Any) -> Any:
    return value.decode() if isinstance(value, (bytes, bytearray)) else value


def _entries(raw):
    return [{_text(k): _text(v) for k, v in fields.items()} for _, fields in raw]


def _decoded(entries, kind):
    from industryflow_contracts import capability_audit as audit
    return [audit.decode(e) for e in entries if kind is None or e.get("kind") == kind]


async def audit_stream(client: Any):
    """Every entry of the capability-use stream, oldest first, as text field/value pairs."""
    from industryflow_contracts import capability_audit as audit
    return _entries(await client.xrange(audit.STREAM_KEY))


async def audit_events(client: Any, kind: Any = None):
    """The stream's events as its writer reads them (``capability_audit.decode``), of ``kind`` if
    given."""
    return _decoded(await audit_stream(client), kind)


def sync_audit_stream(client: Any):
    """``audit_stream`` through a synchronous client."""
    from industryflow_contracts import capability_audit as audit
    return _entries(client.xrange(audit.STREAM_KEY))


def sync_audit_events(client: Any, kind: Any = None):
    """``audit_events`` through a synchronous client."""
    return _decoded(sync_audit_stream(client), kind)


async def pass_audit_windows(client: Any) -> None:
    """As if every per-window record limit had expired: the repeat windows, the unknown-value windows
    and the per-source limits. First-use markers and the repeat counts stay."""
    from industryflow_contracts import capability_audit as audit
    for prefix in (audit.REPEAT_PREFIX, audit.UNKNOWN_REFUSAL_PREFIX, audit.SOURCE_LIMIT_PREFIX):
        keys = [k async for k in client.scan_iter(match=f"{prefix}*")]
        if keys:
            await client.delete(*keys)
