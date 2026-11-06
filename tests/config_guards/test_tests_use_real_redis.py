# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests that need the capability store run on a real Redis in CI (industryflow_contracts.testing).
Both sides fail silently: a test that builds its own fake is green on a timeout a real server raises
(#501), and a CI job that stops providing the server, or stops requiring it, falls back to the fake
without a word. A fake need not come from fakeredis: a class answering the client's commands from a
dict, or re-implementing the store's scripts in Python, hides the same failures."""
import ast
import re
from collections import defaultdict

from _repo import ROOT, load_yaml

from industryflow_contracts import testing

FAKE_CLIENT = re.compile(r"\bfakeredis\.(?:FakeRedis|FakeAsyncRedis|FakeStrictRedis|FakeServer|aioredis)\b")

# Method names that are Redis client commands (redis-py's spelling). A class answering three or more
# of them, itself or through a base class defined in a test file, stands in for the client.
COMMANDS = frozenset({
    "get", "set", "setex", "setnx", "psetex", "getdel", "getex", "getset", "mget", "mset", "msetnx",
    "append", "strlen", "incr", "incrby", "incrbyfloat", "decr", "decrby",
    "delete", "unlink", "exists", "expire", "expireat", "pexpire", "pexpireat", "persist", "ttl", "pttl",
    "type", "rename", "renamenx", "keys", "scan", "scan_iter", "flushdb", "flushall", "ping",
    "hset", "hsetnx", "hget", "hmget", "hmset", "hgetall", "hkeys", "hvals", "hlen", "hdel", "hexists",
    "hincrby", "hscan", "hscan_iter",
    "sadd", "srem", "smembers", "sismember", "smismember", "scard", "spop", "sscan", "sscan_iter",
    "lpush", "rpush", "lpop", "rpop", "lrange", "llen", "ltrim", "lrem", "blpop", "brpop", "lmove",
    "zadd", "zrem", "zrange", "zrangebyscore", "zrevrange", "zscore", "zcard", "zincrby",
    "zremrangebyscore",
    "xadd", "xrange", "xrevrange", "xread", "xreadgroup", "xack", "xlen", "xtrim", "xdel", "xpending",
    "xpending_range", "xclaim", "xautoclaim", "xgroup_create", "xgroup_destroy", "xinfo_stream",
    "eval", "evalsha", "eval_ro", "evalsha_ro", "script_load", "script_exists", "register_script",
    "fcall", "pipeline", "transaction", "watch", "unwatch", "multi", "publish", "pubsub",
})
THRESHOLD = 3

# A class that answers the client's commands and is not a fake of the store: ``path::Class`` to why.
ALLOWED = {
    "services/notebook_sql_proxy/tests/test_backend.py::NoStore":
        "a sentinel: a cancel request must never reach the store, and any record it wrote fails the test",
    "services/notebook_tracking_gateway/tests/test_capability_record.py::_ClaimRace":
        "wraps a real client (testing.async_redis) to hold two claims at a barrier and force the race",
}

SKIP = {"node_modules", ".venv", "venv", "build", "site-packages", ".git"}


def _tests():
    """Every test module and fixture file in the repository (not build output or installed copies)."""
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if SKIP.intersection(rel.parts):
            continue
        if path.name.startswith("test_") or path.name == "conftest.py" or "tests" in rel.parts:
            yield rel.as_posix(), path.read_text(errors="ignore")


def _base_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _defined(cls):
    """The names a class body binds: its methods, and attributes assigned (``get = ...``)."""
    names = set()
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def _test_classes():
    """``path::Class`` to the client commands it answers, its own and those of every base class a test
    file defines under that name (a mixin in a helper module counts)."""
    classes, by_name = {}, defaultdict(list)
    for rel, src in _tests():
        for node in ast.walk(ast.parse(src, filename=rel)):
            if isinstance(node, ast.ClassDef):
                key = f"{rel}::{node.name}"
                classes[key] = (_defined(node), [b for b in map(_base_name, node.bases) if b])
                by_name[node.name].append(key)

    def answers(key, seen):
        own, bases = classes[key]
        found = own & COMMANDS
        for base in bases:
            for other in by_name.get(base, []):
                if other not in seen:
                    found |= answers(other, seen | {other})
        return found

    return {key: answers(key, {key}) for key in classes}


def _hand_fakes():
    return {key: found for key, found in _test_classes().items() if len(found) >= THRESHOLD}


def test_no_test_builds_its_own_fake_store():
    offenders = [rel for rel, src in _tests() if FAKE_CLIENT.search(src)]
    assert not offenders, f"take the store from industryflow_contracts.testing: {offenders}"


def test_no_test_stands_in_for_the_client_with_a_class_of_its_own():
    offenders = {key: sorted(found) for key, found in _hand_fakes().items() if key not in ALLOWED}
    assert not offenders, f"take the store from industryflow_contracts.testing: {offenders}"


def test_every_allowed_stand_in_still_exists_and_still_answers_the_client():
    # An entry for a class that is gone, or no longer looks like a client, would excuse the next one.
    stale = sorted(set(ALLOWED) - set(_hand_fakes()))
    assert not stale, f"no longer a stand-in for the client; remove from ALLOWED: {stale}"


def test_the_check_sees_a_hand_fake():
    src = ("class Mixin:\n    async def eval(self, *a): ...\n    async def xadd(self, *a): ...\n"
           "class Fake(Mixin):\n    async def get(self, key): ...\n")
    [mixin, fake] = [n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.ClassDef)]
    assert len(_defined(fake) & COMMANDS) == 1 and len(_defined(mixin) & COMMANDS) == 2
    # Only through its base does the fake reach the threshold: the inherited commands must count.
    assert len((_defined(fake) | _defined(mixin)) & COMMANDS) >= THRESHOLD


def test_the_unit_test_job_runs_a_real_store_and_requires_it():
    job = load_yaml(".github/workflows/unit-tests.yml")["jobs"]["pytest"]
    assert "redis" in job.get("services", {}), "the pytest job runs no Redis service"
    env = job.get("env", {})
    assert env.get(testing.URL_ENV, "").startswith("redis://localhost:6379")
    assert str(env.get(testing.REQUIRE_ENV)) == "1"
