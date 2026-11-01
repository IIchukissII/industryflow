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
            if any(prefix in value for prefix in PREFIXES):
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
