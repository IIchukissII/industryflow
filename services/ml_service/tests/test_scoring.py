# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""One owner scores engineered rows with a model's detector (ADR-0010 dec 6, ADR-0028 dec 1).

Inference, drift and the deployment gate each called the detector themselves and built its context by
hand; none set the feature names the context declares, so a detector reading them met None on every
path. ``scoring`` now resolves the detector and builds the context, and every path calls it.
"""
import ast
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import scoring  # noqa: E402
from extensions import DetectionResult, register_detector  # noqa: E402

API = Path(__file__).resolve().parents[1] / "api"
SEEN = []


@register_detector("scoring_recorder")
async def _recorder(features, model, threshold, ctx):
    SEEN.append((features, model, threshold, ctx))
    return DetectionResult(score=0.25, is_anomaly=threshold <= 0.25)


def _record(**over):
    return {"model_id": "m1", "detector": "scoring_recorder", **over}


def test_every_row_meets_the_records_detector_with_the_declared_context():
    SEEN.clear()
    spec = scoring.Scoring.of(_record(training_metrics='{"reconstruction_scale": 0.4}'), ("a", "b"), "eq-1")
    results = asyncio.run(scoring.score(spec, "model", [[1, 2], [3, 4]], 0.2))
    assert [r.is_anomaly for r in results] == [True, True]
    assert [(f, m, t) for f, m, t, _ in SEEN] == [([1, 2], "model", 0.2), ([3, 4], "model", 0.2)]
    ctx = SEEN[0][3]
    assert (ctx.feature_names, ctx.equipment_id, ctx.reconstruction_scale) == (["a", "b"], "eq-1", 0.4)


def test_scores_alone_do_not_depend_on_a_threshold():
    spec = scoring.Scoring.of(_record(), ["a"], None)
    assert scoring.scores_blocking(spec, None, [[1], [2]]) == [0.25, 0.25]


def test_a_scoring_travels_as_json_unchanged():
    spec = scoring.Scoring.of(_record(training_metrics={"reconstruction_scale": 2}), ["a", "b"], "eq")
    assert scoring.Scoring.from_json(json.loads(json.dumps(spec.as_json()))) == spec


@pytest.mark.parametrize("metrics", ["not json", "[1, 2]", "null", 7, None, {}])
def test_metrics_that_are_not_an_object_carry_no_scale(metrics):
    assert scoring.Scoring.of(_record(training_metrics=metrics), ["a"], None).reconstruction_scale is None


@pytest.mark.parametrize("detector, refusal", [(None, scoring.NoDetector), ("", scoring.NoDetector),
                                               ("never_registered", scoring.DetectorNotLoaded)])
def test_a_record_naming_no_loaded_detector_is_not_scored(detector, refusal):
    with pytest.raises(refusal):
        scoring.Scoring.of(_record(detector=detector), ["a"], None)
    with pytest.raises(refusal):
        scoring.detector_name(_record(detector=detector))


def test_a_scoring_whose_detector_is_gone_is_refused_when_scored():
    spec = scoring.Scoring("never_registered", ["a"])
    with pytest.raises(scoring.DetectorNotLoaded):
        asyncio.run(scoring.score(spec, None, [[1]], 0.5))


def _calls(name):
    for path in sorted(API.rglob("*.py")):
        rel = str(path.relative_to(API))
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", None)) == name:
                yield rel


@pytest.mark.parametrize("name", ["DetectorContext", "get_detector"])
def test_only_the_scoring_owner_builds_a_context_or_resolves_a_detector(name):
    outside = sorted({rel for rel in _calls(name) if rel != "scoring.py" and not rel.startswith("extensions/")})
    assert outside == [], f"{name} called outside scoring.py: {outside}"
