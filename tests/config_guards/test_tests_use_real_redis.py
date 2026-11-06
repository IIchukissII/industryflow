# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests that need the capability store run on a real Redis in CI (industryflow_contracts.testing).
Both sides fail silently: a test that builds its own fake is green on a timeout a real server raises
(#501), and a CI job that stops providing the server, or stops requiring it, falls back to the fake
without a word. A fake need not come from fakeredis: an object answering one client command from a
dict, or re-implementing the store's scripts in Python, hides the same failures."""
import ast
import re
from collections import defaultdict

from _repo import ROOT, load_yaml

from industryflow_contracts import testing

FAKE_CLIENT = re.compile(r"\bfakeredis\.(?:FakeRedis|FakeAsyncRedis|FakeStrictRedis|FakeServer|aioredis)\b")

# Redis client commands, as redis-py spells its methods.
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
# Names other interfaces use as well (an HTTP session's get, a user manager's delete, a dict's keys).
# One of these answers the client only when it is a callable shaped like the client's: its first
# parameter a store key, and for ``delete`` the client's ``*names`` (an object store deletes one key).
GENERIC = frozenset({"get", "set", "delete", "exists", "keys", "type", "append", "scan", "ping",
                     "publish", "watch", "rename", "persist", "transaction", "multi", "incr", "decr"})
VARARGS_ONLY = frozenset({"delete"})
KEY_PARAMETERS = frozenset({"key", "keys", "name", "names", "k"})
# ``__getattr__`` beside ``execute`` answers every command queued on it: a pipeline stand-in.
ANY, EXECUTE = "__getattr__", "execute"

# Objects that answer client commands and are not fakes of the store: ``path::qualified.Name`` to the
# exact commands it answers and why it is not a fake. A changed answer or a second object of that name
# fails, so an entry never excuses anything but what it names.
ALLOWED = {
    "services/notebook_sql_proxy/tests/test_backend.py::_connected_to.serve.NoStore": (
        {"get"}, "a sentinel: a cancel request must never reach the store; its only lookup finds nothing"),
    "services/notebook_tracking_gateway/tests/test_capability_record.py::_ClaimRace": (
        {"get", "xadd", "eval"},
        "wraps a real client (testing.async_redis), holding two claims at a barrier to force the race"),
    "services/ml_service/tests/test_upload_capability.py::FakeStore": (
        {"set"}, "the upload minter's own one-call port (key, value, ttl_seconds, event), not the client; "
                 "the route's real MULTI runs on testing.redis_pair in the same module"),
    "services/notebook_hub/tests/test_reclaim.py::"
    "test_docker_removes_the_containers_still_mounting_the_volume_first.Client": (
        {"get"}, "the Docker client's volumes.get(name), not the store"),
}

SKIP = {"node_modules", ".venv", "venv", "build", "site-packages", ".git", ".claude"}


def _python_files():
    """Every Python file in the repository (not build output, installed copies or local worktrees)."""
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if not SKIP.intersection(rel.parts):
            yield rel.as_posix(), path.read_text(errors="ignore")


def _is_test(rel):
    parts = rel.split("/")
    return parts[-1].startswith("test_") or parts[-1] == "conftest.py" or "tests" in parts[:-1]


def _tests():
    return ((rel, src) for rel, src in _python_files() if _is_test(rel))


# --- reading what an object answers -------------------------------------------------------------

def _shaped_like_the_client(name, args, bound):
    params = [*args.posonlyargs, *args.args][1 if bound else 0:]
    if name in VARARGS_ONLY:
        return not params and args.vararg is not None and args.vararg.arg.lstrip("_") in KEY_PARAMETERS
    if params:
        return params[0].arg.lstrip("_") in KEY_PARAMETERS
    return args.vararg is not None and args.vararg.arg.lstrip("_") in KEY_PARAMETERS


def _is_hang(node):
    """``await asyncio.sleep(...)`` / ``time.sleep(...)``: a read that never answers."""
    call = node.value if isinstance(node, ast.Await) else node
    return isinstance(call, ast.Call) and getattr(call.func, "attr", getattr(call.func, "id", None)) == "sleep"


def _faults_only(fn):
    """A body that only raises or hangs: a fault injected in place of the store, which models nothing
    about it (an unreachable or hung server)."""
    body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
    return bool(body) and all(isinstance(s, ast.Raise) or (isinstance(s, ast.Expr) and _is_hang(s.value))
                              for s in body)


def _answers(name, value, bound, functions):
    """Whether ``value`` bound under ``name`` on an object answers that client command."""
    if name not in COMMANDS:
        return False
    if isinstance(value, ast.Name) and value.id in functions:
        candidates = functions[value.id]
        if all(_faults_only(f) for f in candidates):
            return False
        return name not in GENERIC or any(_shaped_like_the_client(name, f.args, False) for f in candidates)
    if isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef)) and _faults_only(value):
        return False
    callable_ = isinstance(value, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    if name in GENERIC:
        # Data under a generic name (``self.keys = set(...)``) answers nothing.
        return callable_ and _shaped_like_the_client(name, value.args, bound)
    return True  # a Redis-only command: counted whatever it is bound to


def _class_bindings(cls):
    """(name, value, bound) for each attribute a class body or its methods bind on its instances."""
    for node in cls.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            static = any(getattr(d, "id", None) == "staticmethod" for d in node.decorator_list)
            yield node.name, node, not static
            for inner in ast.walk(node):
                if isinstance(inner, ast.Assign):
                    for t in inner.targets:
                        if (isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                                and t.value.id == "self"):
                            yield t.attr, inner.value, False
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    yield t.id, node.value, True


def _call_name(call):
    return getattr(call.func, "attr", getattr(call.func, "id", None))


def _namespace_bindings(call):
    """(name, value, bound) for ``SimpleNamespace(get=...)`` and ``type("F", (), {"get": ...})``."""
    if _call_name(call) == "SimpleNamespace":
        return [(kw.arg, kw.value, False) for kw in call.keywords if kw.arg]
    if _call_name(call) == "type" and len(call.args) == 3 and isinstance(call.args[2], ast.Dict):
        return [(k.value, v, True) for k, v in zip(call.args[2].keys, call.args[2].values)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
    return []


def _objects(rel, src):
    """``(key, line, bases, bindings)`` for every class and namespace object in one module."""
    tree = ast.parse(src, filename=rel)
    functions = defaultdict(list)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name].append(node)
    found = []

    def visit(node, scope):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                qual = ".".join([*scope, child.name])
                bases = [n for n in (getattr(b, "attr", getattr(b, "id", None)) for b in child.bases) if n]
                found.append((f"{rel}::{qual}", child.lineno, child.name, bases,
                              [(n, v, b) for n, v, b in _class_bindings(child)], functions))
                visit(child, [*scope, child.name])
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visit(child, [*scope, child.name])
            else:
                if isinstance(child, ast.Call):
                    bindings = _namespace_bindings(child)
                    if bindings:
                        qual = ".".join([*scope, f"{_call_name(child)}@{child.lineno}"])
                        found.append((f"{rel}::{qual}", child.lineno, None, [], bindings, functions))
                visit(child, scope)

    visit(tree, [])
    return found


def _scan(files, tests=None):
    """``[(key, line, commands)]`` for each object a test module defines that answers client commands,
    its own or through a base class defined anywhere in ``files`` (``rel -> source``)."""
    is_test = tests or _is_test
    objects = [o for rel, src in files.items() for o in _objects(rel, src)]
    by_name = defaultdict(list)
    for o in objects:
        if o[2]:
            by_name[o[2]].append(o)

    def answered(obj, seen):
        _key, _line, _name, bases, bindings, functions = obj
        out = {n for n, v, b in bindings if _answers(n, v, b, functions)}
        names = {n for n, _v, _b in bindings}
        if ANY in names and EXECUTE in names:
            out.add(ANY)
        for base in bases:
            for other in by_name.get(base, []):
                if id(other) not in seen:
                    out |= answered(other, seen | {id(other)})
        return out

    hits = []
    for obj in objects:
        if is_test(obj[0].split("::")[0]):
            commands = answered(obj, {id(obj)})
            if commands:
                hits.append((obj[0], obj[1], commands))
    return hits


def _verdict(hits, allowed):
    """``(offenders, stale)``: stand-ins nobody allowed, and allowed entries that do not name exactly
    one object answering exactly the commands recorded for it."""
    by_key = defaultdict(list)
    for key, line, commands in hits:
        by_key[key].append((line, commands))
    offenders = {f"{key}:{line}": sorted(c) for key, found in by_key.items() if key not in allowed
                 for line, c in found}
    stale = sorted(key for key, (commands, _why) in allowed.items()
                   if [c for _line, c in by_key.get(key, [])] != [set(commands)])
    return offenders, stale


def _repository():
    return _verdict(_scan(dict(_python_files())), ALLOWED)


def test_no_test_builds_its_own_fake_store():
    offenders = [rel for rel, src in _tests() if FAKE_CLIENT.search(src)]
    assert not offenders, f"take the store from industryflow_contracts.testing: {offenders}"


def test_no_test_stands_in_for_the_client_with_an_object_of_its_own():
    offenders, _ = _repository()
    assert not offenders, f"take the store from industryflow_contracts.testing: {offenders}"


def test_every_allowed_stand_in_names_exactly_what_it_excuses():
    _, stale = _repository()
    assert not stale, f"no longer one object answering the recorded commands; update ALLOWED: {stale}"


# --- the check itself, on sources written to evade it ------------------------------------------

def _flagged(src, extra=None, allowed=None):
    files = {"svc/tests/test_x.py": src, **(extra or {})}
    return _verdict(_scan(files), allowed or {})


EVASIONS = {
    "one generic command over a dict": "class S:\n    async def get(self, key): return self.kv.get(key)\n",
    "a write pair": "class S:\n    async def set(self, key, v): ...\n    async def delete(self, *keys): ...\n",
    "a delete of many keys": "class S:\n    async def delete(self, *keys): return len(keys)\n",
    "a script re-implemented": "class S:\n    async def eval(self, script, n, *a): return 1\n",
    "a catch-all pipeline": ("class P:\n    def __getattr__(self, name): return lambda *a: None\n"
                             "    async def execute(self): return []\n"),
    "a namespace": "from types import SimpleNamespace\nS = SimpleNamespace(get=lambda key: None)\n",
    "a namespace of a local function":
        "from types import SimpleNamespace\nasync def get(key): return None\nS = SimpleNamespace(get=get)\n",
    "a namespace with a script": "import types\nS = types.SimpleNamespace(eval=lambda *a: 1)\n",
    "bound in __init__": "class S:\n    def __init__(self):\n        self.get = lambda key: None\n",
    "built with type()": "S = type('S', (), {'get': lambda self, key: None})\n",
    "a fail-sometimes store":
        "class S:\n    async def exists(self, key):\n        if self.fail:\n            raise OSError\n        return 1\n",
    "a nested class": "def f():\n    class S:\n        async def hkeys(self, key): return []\n    return S\n",
}


def test_the_check_sees_each_evasion():
    missed = [what for what, src in EVASIONS.items() if not _flagged(src)[0]]
    assert not missed, f"not seen: {missed}"


def test_the_check_sees_commands_inherited_from_a_mixin_outside_the_tests():
    mixin = {"svc/helpers.py": "class Mixin:\n    async def xadd(self, name, fields): ...\n"}
    offenders, _ = _flagged("from helpers import Mixin\nclass S(Mixin):\n    pass\n", mixin)
    assert list(offenders) == ["svc/tests/test_x.py::S:2"]


def test_the_check_leaves_other_interfaces_and_injected_faults_alone():
    src = ("class Manager:\n    async def get(self, user_id): ...\n    async def delete(self, user): ...\n"
           "class Http:\n    def get(self, url, params=None): ...\n"
           "class Down:\n    async def get(self, key):\n        raise ConnectionError('down')\n"
           "class Hung:\n    async def exists(self, key):\n        await asyncio.sleep(60)\n"
           "class Objects:\n    def __init__(self):\n        self.keys = set()\n    def delete(self, key): ...\n"
           "class Row:\n    def __getattr__(self, name): return None\n")
    assert _flagged(src) == ({}, [])


def test_an_allowance_excuses_one_object_with_exactly_its_commands():
    src = "class S:\n    async def get(self, key): ...\n"
    allowed = {"svc/tests/test_x.py::S": ({"get"}, "why")}
    assert _flagged(src, allowed=allowed) == ({}, [])
    # A second object of the same name, a changed answer, and a vanished object are each refused.
    assert _flagged(src + src, allowed=allowed)[1] == ["svc/tests/test_x.py::S"]
    assert _flagged("class S:\n    async def get(self, key): ...\n    async def eval(self, *a): ...\n",
                    allowed=allowed)[1] == ["svc/tests/test_x.py::S"]
    assert _flagged("", allowed=allowed)[1] == ["svc/tests/test_x.py::S"]


def test_the_unit_test_job_runs_a_real_store_and_requires_it():
    job = load_yaml(".github/workflows/unit-tests.yml")["jobs"]["pytest"]
    assert "redis" in job.get("services", {}), "the pytest job runs no Redis service"
    env = job.get("env", {})
    assert env.get(testing.URL_ENV, "").startswith("redis://localhost:6379")
    assert str(env.get(testing.REQUIRE_ENV)) == "1"
