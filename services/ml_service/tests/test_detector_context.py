# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every scoring path builds the detector's context in one place (ADR-0010 dec 6).

The context declared feature names, but inference, drift and the deploy gate each built it by hand
and none set them: a detector reading them met None on every path. The fields now come from
``extensions.detector_context_fields`` only; this guard keeps a new path from building its own.
"""
import ast
from pathlib import Path

API = Path(__file__).resolve().parents[1] / "api"
# Rebuild the context from fields the deploy gate made with the helper (its plan, and the probe it sends
# them to as JSON); test_the_deploy_gate_builds_its_context_fields_from_the_helper holds the first.
FROM_SERIALIZED = {"load_probe_worker.py", "baseline_gate.py"}


def _constructions():
    for path in sorted(API.rglob("*.py")):
        rel = str(path.relative_to(API))
        if rel.startswith("extensions/"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == "DetectorContext":
                yield rel, node


def test_every_detector_context_is_built_from_the_helper():
    found = list(_constructions())
    assert {rel for rel, _ in found} >= {"routers/inference.py", "routers/drift.py"}, "the guard found no call site"
    by_hand = [f"{rel}:{node.lineno}" for rel, node in found
               if rel not in FROM_SERIALIZED and not (
                   len(node.args) == 0 and len(node.keywords) == 1 and node.keywords[0].arg is None
                   and isinstance(node.keywords[0].value, ast.Call)
                   and getattr(node.keywords[0].value.func, "id", None) == "detector_context_fields")]
    assert by_hand == [], f"a DetectorContext built by hand instead of from detector_context_fields: {by_hand}"


def test_the_deploy_gate_builds_its_context_fields_from_the_helper():
    source = (API / "baseline_gate.py").read_text()
    assert "context=detector_context_fields(" in source
