# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Training endpoints.

The UI can submit a training request here, but automated end-to-end training
(pull history -> fit -> log to MLflow -> register) is not yet implemented on this
deployment. Rather than silently 404 (the route used to point at a service that
doesn't host it) or fake a success, this endpoint authenticates the caller and
returns an explicit 501 so the UI can show an honest message. Models are currently
produced via the MLflow / notebook workflow (services/ml_service/notebooks).
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

import auth

router = APIRouter(prefix="/api/training", tags=["Training"])


class TrainingConfig(BaseModel):
    company_id: Optional[str] = None
    sensor_id: Optional[str] = None
    sensor_type: Optional[str] = None
    contamination: Optional[float] = None
    n_estimators: Optional[int] = None
    lookback_days: Optional[int] = None


class TrainingStartRequest(BaseModel):
    config: TrainingConfig
    description: Optional[str] = None


@router.post("/start", status_code=501)
async def start_training(
    request: TrainingStartRequest,
    current_user: dict = Depends(auth.verify_jwt_token),
):
    """Submit a training request. Returns 501 until automated training is wired up."""
    raise HTTPException(
        status_code=501,
        detail=(
            "Automated model training is not yet implemented on this deployment. "
            "Train and register models via the MLflow / notebook workflow; they will "
            "then appear in this list."
        ),
    )
