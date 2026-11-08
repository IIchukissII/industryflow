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
# Rebuild the context from fields the helper made: the deploy gate from its plan, the probe worker from
# the JSON the gate sends it. There only `DetectorContext(**<fields>)` is allowed, never a named field.
FROM_SERIALIZED = {"load_probe_worker.py", "baseline_gate.py"}


def _name(func):
    return getattr(func, "id", getattr(func, "attr", None))


def _constructions():
    for path in sorted(API.rglob("*.py")):
        rel = str(path.relative_to(API))
        if rel.startswith("extensions/"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and _name(node.func) == "DetectorContext":
                yield rel, node


def _fields_only(node):
    """DetectorContext(**<expr>): no positional argument and no named field."""
    return not node.args and len(node.keywords) == 1 and node.keywords[0].arg is None


def _from_helper(expr):
    return isinstance(expr, ast.Call) and _name(expr.func) == "detector_context_fields"


def test_every_detector_context_is_built_from_the_helper():
    found = list(_constructions())
    assert {rel for rel, _ in found} >= {"routers/inference.py", "routers/drift.py", *FROM_SERIALIZED}, \
        "the guard found no call site"
    by_hand = [f"{rel}:{node.lineno}" for rel, node in found
               if not _fields_only(node)
               or (rel not in FROM_SERIALIZED and not _from_helper(node.keywords[0].value))]
    assert by_hand == [], f"a DetectorContext built by hand instead of from detector_context_fields: {by_hand}"


def test_the_deploy_gate_takes_its_context_fields_from_the_helper():
    tree = ast.parse((API / "baseline_gate.py").read_text())
    contexts = [kw.value for node in ast.walk(tree) if isinstance(node, ast.Call) and _name(node.func) == "Plan"
                for kw in node.keywords if kw.arg == "context"]
    assert contexts, "the deploy gate builds no plan with a context"
    assert all(_from_helper(c) for c in contexts), "a plan's context built by hand"


def test_metrics_that_are_not_an_object_carry_no_scale():
    import sys
    sys.path.insert(0, str(API))
    from extensions import detector_context_fields

    for metrics in ("not json", "[1, 2]", "null", 7, None, {}):
        assert detector_context_fields({"training_metrics": metrics}, ["a"])["reconstruction_scale"] is None
    assert detector_context_fields({"training_metrics": '{"reconstruction_scale": 2}'}, ["a"],
                                   "eq")["reconstruction_scale"] == 2
