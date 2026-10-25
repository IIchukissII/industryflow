# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Drift evaluation endpoint (ADR-0021 decision 3).

The alert service's scheduled drift evaluator delegates the statistical compute here,
exactly as the real-time ml rules delegate scoring to /api/inference: given a model and a
trailing window, this reads the tenant's recent sensor data, compares it against the
model's stored reference profile with evidently, and returns the drift scores. The alert
service owns the decision (applies each rule's threshold and fires the statistical alert);
this endpoint owns the ML math and the tenant data path.

A model with no reference profile returns status "unavailable" — honest, not a fake zero.

Prediction drift (ADR-0021 dec 1) needs the model's scores over the CURRENT window, which nothing
stores: the endpoint produces them by scoring a sample of the window's rows the way ``/predict``
scores one reading — the model's feature config, then the detector its record names. Where that
cannot be done (no prediction baseline, no feature config, a model that cannot be loaded or
interpreted), data drift is still reported and the response says why prediction drift is not.
"""
from datetime import datetime, timedelta, timezone
import asyncio
import logging
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict

from drift import (
    align_readings, compute_drift, prediction_column_refusal, DEFAULT_DRIFT_SHARE_THRESHOLD, MIN_ROWS,
)
from extensions import DetectorContext, UninterpretableModel
from feature_engineering import FeatureEngineeringEngine
from .inference import _resolve_company_id, detector_for, load_model_or_refuse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drift", tags=["Drift"])

# Default trailing window: one day of readings. Configurable per call; the cadence and the
# authoritative threshold are the alert rule's (ADR-0021 dec 2-3, 5).
DEFAULT_WINDOW_MINUTES = 24 * 60

# How many of the window's rows are scored for prediction drift. Every row is one model call; a day
# of per-second readings is tens of thousands. An evenly spaced sample keeps the cost bounded and the
# distribution representative, as the reference profile's own sample does.
MAX_PREDICTION_ROWS = 500


def _even_sample(n: int, k: int) -> List[int]:
    """Deterministic, evenly spaced indices — the same rule the reference profile samples with."""
    if n <= k:
        return list(range(n))
    return np.linspace(0, n - 1, k).astype(int).tolist()


def _score_rows(rows, model, detector, ctx) -> List[float]:
    """Score engineered rows through the detector. Run in a worker thread with its own loop: a
    detector is async by contract but does its model work synchronously, and hundreds of them must
    not hold the service's event loop."""
    async def run():
        return [(await detector(features, model, 0.5, ctx)).score for features in rows]
    return asyncio.run(run())


async def _current_predictions(
    request: Request, company_id: str, model: Dict[str, Any], readings: List[Dict[str, Any]],
):
    """The model's 0-1 scores over a sample of the current window, or ``(None, reason)``."""
    profile = model.get("reference_profile") or {}
    if not profile.get("prediction_column"):
        return None, "the reference profile captured no prediction scores"
    refusal = prediction_column_refusal(profile)
    if refusal:
        # Not a baseline, so nothing is scored against it (ADR-0021 dec 1).
        return None, refusal

    repository = request.app.state.ml_repository
    feature_config_id = model.get("feature_config_id")
    if not feature_config_id:
        return None, "the model has no feature config, so its inputs cannot be engineered"
    feature_config = await repository.get_feature_config_by_id(
        company_id=company_id, config_id=feature_config_id,
    )
    if not feature_config:
        return None, f"feature config {feature_config_id} not found"

    base_sensors = feature_config.get("base_sensors") or []
    if not base_sensors:
        return None, "the feature config names no base sensors"
    _, aligned = align_readings(readings, base_sensors)
    n = len(aligned[base_sensors[0]])
    if n < MIN_ROWS:
        return None, (f"only {n} instants in the window carry every base sensor; "
                      f"need >= {MIN_ROWS}")

    try:
        _, detector = detector_for(model)
        loaded = await load_model_or_refuse(company_id, model)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else exc.detail.get("message", str(exc.detail))
        return None, f"the model cannot be scored: {detail}"

    equipment_id = model.get("equipment_id")
    engine = FeatureEngineeringEngine(
        feature_config=feature_config,
        baseline_provider=getattr(request.app.state, "baseline_provider", None),
        equipment_id=equipment_id,
        company_id=company_id,
        kill_switch=getattr(request.app.state, "stateful_switch", None),
    )
    rows = []
    for i in _even_sample(n, MAX_PREDICTION_ROWS):
        rows.append(await engine.transform({s: aligned[s][i] for s in base_sensors}))

    training_metrics = model.get("training_metrics") or {}
    ctx = DetectorContext(
        equipment_id=equipment_id,
        reconstruction_scale=training_metrics.get("reconstruction_scale"),
    )
    try:
        scores = await asyncio.to_thread(_score_rows, rows, loaded, detector, ctx)
    except UninterpretableModel as exc:
        return None, f"the model's output cannot be interpreted: {exc}"
    return scores, None


class DriftEvaluateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: str = Field(..., description="Model to evaluate drift for")
    company_id: Optional[str] = Field(
        None,
        description="Tenant company_id; honoured only for authenticated internal service "
                    "calls (X-Internal-Service-Token), mirroring /api/inference",
    )
    window_minutes: int = Field(
        DEFAULT_WINDOW_MINUTES, gt=0,
        description="Trailing window (minutes) of recent data to compare against the baseline",
    )
    drift_share_threshold: float = Field(
        DEFAULT_DRIFT_SHARE_THRESHOLD, ge=0.0, le=1.0,
        description="Advisory threshold on the drifted-feature share; the alert rule's "
                    "threshold is authoritative",
    )


@router.post("/evaluate")
async def evaluate_drift(request_data: DriftEvaluateRequest, request: Request):
    """Evaluate data/prediction drift for one model over a trailing window.

    Auth mirrors /api/inference: a user JWT, or the alert worker's internal service token
    (then company_id comes from the body). Tenant-scoped throughout (ADR-0003/0021 dec 6).
    """
    repository = request.app.state.ml_repository

    company_id = await _resolve_company_id(request, request_data)

    model = await repository.get_model_by_id(
        company_id=company_id, model_id=request_data.model_id
    )
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    reference_profile = model.get("reference_profile")
    if not reference_profile:
        # No baseline captured (model predates ADR-0021 or was registered without one).
        return {
            "model_id": request_data.model_id,
            "status": "unavailable",
            "reason": "model has no reference profile; retrain to enable drift monitoring",
        }

    sensor_ids = model.get("sensor_ids") or []
    if not sensor_ids:
        return {
            "model_id": request_data.model_id,
            "status": "unavailable",
            "reason": "model has no sensor_ids; cannot read a comparison window",
        }

    end = datetime.now(timezone.utc)
    start = end - timedelta(minutes=request_data.window_minutes)

    try:
        readings = await repository.read_sensor_window(
            company_id=company_id, sensor_ids=sensor_ids, start=start, end=end,
        )
    except Exception as e:
        logger.error(f"Drift window read failed for model {request_data.model_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read comparison window")

    # Rows, not per-sensor lists: an instant is compared only where every reference column reported
    # at it. Sensors reporting at different rates would otherwise yield columns of unequal length.
    present = {r["sensor_name"] for r in readings}
    compared = [c for c in reference_profile.get("columns", []) if c in present]
    _, window = align_readings(readings, compared)

    predictions, prediction_reason = await _current_predictions(request, company_id, model, readings)

    try:
        # evidently is CPU-bound and synchronous; off the loop, like every other blocking call here.
        result = await asyncio.to_thread(
            compute_drift, reference_profile, window,
            current_predictions=predictions,
            drift_share_threshold=request_data.drift_share_threshold,
        )
    except ValueError as e:
        # Unreadable/old reference-profile shape — surface as unavailable, not a 500.
        logger.warning(f"Drift compute rejected profile for model {request_data.model_id}: {e}")
        return {"model_id": request_data.model_id, "status": "unavailable", "reason": str(e)}
    except Exception as e:
        # Degenerate window (e.g. a constant/empty column evidently cannot test) must not
        # 500 the scheduler — report it so the evaluator skips this cycle and moves on.
        logger.error(f"Drift compute failed for model {request_data.model_id}: {e}", exc_info=True)
        return {"model_id": request_data.model_id, "status": "error", "reason": str(e)}

    result["model_id"] = request_data.model_id
    result["window_minutes"] = request_data.window_minutes
    if result.get("prediction_drift") is None and prediction_reason and not result.get("prediction_drift_reason"):
        result["prediction_drift_reason"] = prediction_reason
    logger.info(
        "Drift evaluated model=%s status=%s share=%s",
        request_data.model_id, result.get("status"),
        result.get("data_drift", {}).get("drift_share"),
    )
    return result
