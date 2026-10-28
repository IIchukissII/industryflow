# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The prediction baseline, derived at the deployment gate (ADR-0035 dec 2, 3, 5).

Prediction drift compares the declared detector's scores on the live window with its scores on the
model's feature sample. Only the platform can guarantee both come from the same detector on the same
serving stack, so the sample is scored here, at every deployment, the way `/predict` scores a reading:
each complete row through the model's feature configuration, then the detector its record names.

The load that scores it is the gate's own. An uploaded model is loaded only in the isolated probe
(ADR-0030 dec 7), so its rows are engineered here and scored there, in the same load that proves it
works; any other model is loaded the way serving loads it. A detector that cannot score the sample
leaves the model without a prediction baseline and says why; data drift still runs on the sample.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np

import drift
from extensions import DetectorContext, UninterpretableModel
from feature_engineering import FeatureEngineeringEngine

# The detector's threshold does not change its score; the gate asks only for the score.
_THRESHOLD = 0.5


def score_rows(rows, model, detector, ctx) -> List[float]:
    """Score engineered rows through the detector. Run in a worker thread with its own loop: a detector
    is async by contract but does its model work synchronously, and hundreds of them must not hold the
    service's event loop."""
    async def run():
        return [(await detector(features, model, _THRESHOLD, ctx)).score for features in rows]
    return asyncio.run(run())


@dataclass
class Plan:
    """A model's sample, ready to score: the engineered rows, or why there is nothing to score."""

    profile: Dict[str, Any]
    detector: Optional[str] = None
    context: Dict[str, Any] = field(default_factory=dict)
    rows: List[np.ndarray] = field(default_factory=list)
    reason: Optional[str] = None

    def detector_context(self) -> DetectorContext:
        return DetectorContext(**self.context)

    def for_probe(self) -> Optional[Dict[str, Any]]:
        """What the isolated probe needs to score the rows in its own load, or None."""
        if self.reason or not self.rows:
            return None
        return {"detector": self.detector, "context": self.context,
                "rows": [r.reshape(-1).tolist() for r in self.rows]}

    def finish(self, scores: Optional[List[float]], error: Optional[str]) -> Dict[str, Any]:
        """The profile to store, with derived scores or with the reason there are none."""
        if self.reason:
            return drift.without_prediction_baseline(self.profile, self.reason)
        if error or not scores:
            return drift.without_prediction_baseline(
                self.profile, f"the model's detector could not score its sample: {error or 'no scores'}")
        try:
            return drift.with_derived_scores(self.profile, scores,
                                             derived_at=datetime.now(timezone.utc).isoformat())
        except ValueError as exc:
            return drift.without_prediction_baseline(self.profile, str(exc))


async def plan(app_state, repository, company_id: str, model: Dict[str, Any]) -> Optional[Plan]:
    """The model's sample engineered for scoring, or None when it has no accepted sample."""
    profile = model.get("reference_profile")
    if not profile or profile.get("refused"):
        return None
    feature_config = None
    if model.get("feature_config_id"):
        feature_config = await repository.get_feature_config_by_id(
            company_id=company_id, config_id=model["feature_config_id"],
        )
    if not feature_config:
        return Plan(profile, reason="the model has no feature configuration, so its sample cannot be scored")
    base_sensors = feature_config.get("base_sensors") or []
    if sorted(profile.get("columns") or []) != sorted(base_sensors):
        # A baseline recorded before ADR-0035 dec 4, or a binding that has since changed.
        return Plan(profile, reason=(f"the sample's columns are not the feature configuration's inputs "
                                     f"{sorted(base_sensors)}"))
    rows = drift.sample_rows(profile)
    if not rows:
        return Plan(profile, reason="the sample has no complete row to score")

    engine = FeatureEngineeringEngine(
        feature_config=feature_config,
        baseline_provider=getattr(app_state, "baseline_provider", None),
        equipment_id=model.get("equipment_id"),
        company_id=company_id,
        kill_switch=getattr(app_state, "stateful_switch", None),
    )
    training_metrics = model.get("training_metrics") or {}
    return Plan(
        profile,
        detector=model.get("detector"),
        context={"equipment_id": model.get("equipment_id"),
                 "reconstruction_scale": training_metrics.get("reconstruction_scale")},
        rows=[await engine.transform(row) for row in rows],
    )


async def score_in_process(p: Plan, loaded_model, detector) -> Dict[str, Any]:
    """Score the plan's rows with a model this process loaded, and the profile that results."""
    if p.reason:
        return p.finish(None, None)
    try:
        scores = await asyncio.to_thread(score_rows, p.rows, loaded_model, detector, p.detector_context())
    except UninterpretableModel as exc:
        return p.finish(None, str(exc))
    return p.finish(scores, None)
