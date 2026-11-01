# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""No service spells the ended-session record's keys itself (ADR-0004 dec 2).

The gateway writes the record and every verifier of an access token reads it. A copy of a key that
drifts fails silently: its reader finds no record, so every ended session stays open, and each side's
own tests still pass. The keys are defined once, in industryflow_contracts.sessions. Docstrings and
comments may name them; code may not.

Both sides: the same scan finds the definitions in the package, or it proves nothing.
"""
import ast

from _repo import ROOT
from industryflow_contracts import sessions

PREFIXES = (sessions.ENDED_SESSION_PREFIX, sessions.USER_ENDED_PREFIX)
PACKAGE = ROOT / "libs" / "industryflow_contracts" / "industryflow_contracts"


def _code_literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstrings.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.lineno, node.value


def _spellings(paths):
    found = []
    for path in paths:
        for line, value in _code_literals(path):
            if any(prefix.rstrip(":") in value for prefix in PREFIXES):
                found.append(f"{path.relative_to(ROOT)}:{line}")
    return found


def _service_sources():
    for path in sorted((ROOT / "services").rglob("*.py")):
        if {"tests", "node_modules", ".venv"}.intersection(path.relative_to(ROOT).parts):
            continue
        yield path


def test_no_service_spells_the_record_keys():
    offenders = _spellings(_service_sources())
    assert not offenders, f"take the keys from industryflow_contracts.sessions: {offenders}"


def test_the_scan_finds_the_package_definitions():
    assert len(_spellings([PACKAGE / "sessions.py"])) == len(PREFIXES)


def test_every_verifier_of_an_access_token_reads_the_record():
    readers = {
        "services/api_gateway/messaging/refresh_tokens.py": "sessions.session_ended(",
        "services/api_gateway/users.py": "refresh_tokens.session_ended(",
        "services/ml_service/api/auth.py": "sessions.session_ended(",
    }
    missing = [rel for rel, call in readers.items() if call not in (ROOT / rel).read_text(encoding="utf-8")]
    assert not missing, f"no ended-session check in: {missing}"


# --- one store: a reader on another database finds no record and admits every ended session ------

SHARED_STORE = "redis://redis:6379/0"


def _compose_env():
    from _repo import load_yaml

    for name, service in load_yaml("docker-compose.yml")["services"].items():
        env = service.get("environment") or {}
        if isinstance(env, dict):
            yield name, env


def test_every_compose_service_names_the_one_store():
    urls = {(name, key): str(value) for name, env in _compose_env() for key, value in env.items()
            if str(value).startswith("redis://") or "REDIS_URL:-redis://" in str(value)}
    assert any(key == "SESSION_REDIS_URL" for _, key in urls), "the scan finds no session-store URL"
    wrong = {k: v for k, v in urls.items() if SHARED_STORE not in v}
    assert not wrong, f"a Redis URL that is not {SHARED_STORE}: {wrong}"
    gateway = dict(_compose_env())["api-gateway"]
    assert (gateway["REDIS_HOST"], str(gateway["REDIS_PORT"]), str(gateway["REDIS_DB"])) == ("redis", "6379", "0")


def test_helm_names_the_one_store_for_the_gateway_and_the_session_readers():
    lines = (ROOT / "deploy/helm/industryflow/templates/configmap.yaml").read_text(encoding="utf-8").splitlines()
    value = {line.split(":", 1)[0].strip(): line.split(":", 1)[1].strip() for line in lines
             if line.startswith("  ") and not line.strip().startswith(("#", "{{")) and ":" in line}
    assert value["SESSION_REDIS_URL"] == value["REDIS_URL"]
    assert value["REDIS_DB"] == '"0"' and value["REDIS_URL"].endswith('"redis://%s:%v/0" (include "industryflow.redisHost" .) .Values.infra.redis.port | quote }}')
    assert value["REDIS_PORT"] == "{{ .Values.infra.redis.port | quote }}"
