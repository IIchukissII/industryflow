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
"""
from datetime import datetime, timedelta, timezone
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, ConfigDict

from drift import compute_drift, DEFAULT_DRIFT_SHARE_THRESHOLD
from .inference import _resolve_company_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/drift", tags=["Drift"])

# Default trailing window: one day of readings. Configurable per call; the cadence and the
# authoritative threshold are the alert rule's (ADR-0021 dec 3 / deferred config).
DEFAULT_WINDOW_MINUTES = 24 * 60


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
        window = await repository.read_sensor_window(
            company_id=company_id, sensor_ids=sensor_ids, start=start, end=end,
        )
    except Exception as e:
        logger.error(f"Drift window read failed for model {request_data.model_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read comparison window")

    try:
        result = compute_drift(
            reference_profile, window,
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
    logger.info(
        "Drift evaluated model=%s status=%s share=%s",
        request_data.model_id, result.get("status"),
        result.get("data_drift", {}).get("drift_share"),
    )
    return result
