# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A test drops a tenant schema it made only through ``industryflow_contracts.testing.drop_tenant_schema_sql``.

A bare ``DROP SCHEMA ... CASCADE`` leaves the hypertables' compressed chunks, which live outside the
schema and hold grants to the tenant's reader role, so the role's drop that follows fails whenever the
background compression job has run: a teardown that fails at random, in whichever proof ran last.
"""
from __future__ import annotations

import re

from _repo import ROOT

# Split so this guard does not find itself.
BARE = re.compile("DROP SCHEMA" + r"\s+IF\s+EXISTS", re.I)
HELPER = ROOT / "libs" / "industryflow_contracts" / "industryflow_contracts" / "testing.py"


def _python_sources():
    for path in ROOT.rglob("*.py"):
        parts = set(path.relative_to(ROOT).parts)
        if parts & {".venv", "node_modules", "build", "__pycache__"}:
            continue
        yield path


def test_no_test_drops_a_tenant_schema_by_itself():
    bare = sorted(str(p.relative_to(ROOT)) for p in _python_sources()
                  if p != HELPER and ("tests" in p.parts or p.name.startswith("test_")) and BARE.search(p.read_text()))
    assert not bare, f"tenant schemas dropped without their hypertables' chunks: {bare}"


def test_the_helper_drops_the_hypertables_first():
    text = HELPER.read_text()
    assert text.index("DROP TABLE %I.%I CASCADE") < BARE.search(text).start()
