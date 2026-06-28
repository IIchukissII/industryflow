# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Inference Router
Real-time ML inference endpoint for anomaly detection in sensor data
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
import logging
import mlflow
import mlflow.pyfunc
import os
import hmac
import uuid

import auth
from feature_engineering import FeatureEngineeringEngine
from extensions import get_detector, DetectorContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inference", tags=["Inference"])

MLFLOW_TRACKING_URI = os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow:5000')
mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)


# ============================================================================
# Pydantic Models
# ============================================================================

class InferenceRequest(BaseModel):
    """Request for ML inference"""
    model_id: str = Field(..., description="Model ID from database")
    sensor_data: Dict[str, Any] = Field(..., description="Sensor reading data")
    threshold: float = Field(default=0.85, ge=0.0, le=1.0, description="Anomaly threshold")
    company_id: Optional[str] = Field(None, description="Tenant company_id; honoured only for authenticated internal service calls (see X-Internal-Service-Token)")


class InferenceResponse(BaseModel):
    """Response from ML inference"""
    model_id: str
    prediction: float = Field(..., description="Anomaly score (0-1)")
    is_anomaly: bool = Field(..., description="Whether value exceeds threshold")
    threshold: float
    model_version: Optional[str] = None


# ============================================================================
# Dependency Injection
# ============================================================================

async def _company_id_from_user(request: Request, user_id: str) -> str:
    """Look up a user's company_id across the tenant schemas."""
    db_pool = request.app.state.db_pool

    async with db_pool.acquire() as conn:
        # Get all tenant schemas
        schemas = await conn.fetch("""
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name LIKE 'tenant_%'
            ORDER BY schema_name
        """)

        # Search each tenant schema for the user
        for schema_row in schemas:
            schema_name = schema_row['schema_name']

            await conn.execute(f"SET search_path TO {schema_name}, public")

            company_id = await conn.fetchval(
                'SELECT company_id FROM "user" WHERE id = $1',
                user_id
            )

            if company_id:
                return str(company_id)

        raise HTTPException(
            status_code=404,
            detail="User not found or not associated with any company"
        )


async def get_company_id_dependency(
    request: Request,
    current_user: dict = Depends(auth.verify_jwt_token)
) -> str:
    """Resolve the caller's company_id from their verified JWT."""
    # Prefer the company_id claim (ADR-0003 dec 2; avoids the X2 tenant-schema scan).
    claim = current_user.get("payload", {}).get("company_id")
    if claim:
        return str(claim)
    return await _company_id_from_user(request, current_user["user_id"])


async def _resolve_company_id(request: Request, request_data: InferenceRequest) -> str:
    """
    Resolve the tenant for an inference request — authentication required.

    - Internal service-to-service callers (e.g. the alert worker) present a valid
      X-Internal-Service-Token (shared secret INTERNAL_SERVICE_TOKEN); the company_id is
      then read from the request body and validated as a UUID.
    - All other callers must present a user JWT; the company_id is derived from it.

    If INTERNAL_SERVICE_TOKEN is unset the internal path is disabled (fail closed). This
    is an interim service-to-service mechanism; ADR-0004/ADR-0002 defer the full design.
    """
    internal_token = os.getenv("INTERNAL_SERVICE_TOKEN")
    presented = request.headers.get("X-Internal-Service-Token")
    if internal_token and presented and hmac.compare_digest(presented, internal_token):
        if not request_data.company_id:
            raise HTTPException(status_code=400, detail="company_id required for internal service calls")
        try:
            return str(uuid.UUID(str(request_data.company_id)))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(status_code=400, detail="company_id must be a valid UUID")

    # Otherwise require a user JWT.
    user_data = await auth.verify_jwt_token(request.headers.get("Authorization"))
    claim = user_data.get("payload", {}).get("company_id")
    if claim:
        return str(claim)
    return await _company_id_from_user(request, user_data["user_id"])


# ============================================================================
# Helper Functions
# ============================================================================

def load_model_from_mlflow(mlflow_run_id: str):
    """Load model from MLflow using run_id"""
    try:
        model_uri = f"runs:/{mlflow_run_id}/model"
        model = mlflow.pyfunc.load_model(model_uri)
        logger.info(f"Model loaded from MLflow: {model_uri}")
        return model
    except Exception as e:
        logger.error(f"Failed to load model from MLflow: {e}")
        raise




# ============================================================================
# API Endpoints
# ============================================================================

@router.post("/predict", response_model=InferenceResponse)
async def predict(
    request_data: InferenceRequest,
    request: Request
):
    """
    Run ML inference on sensor data and return the anomaly score and threshold check.

    Requires authentication: either a user JWT, or a valid internal service token used by
    the alert worker. The tenant is resolved by _resolve_company_id and is never trusted
    from an unauthenticated request body (ADR-0004 decisions 4-5, ADR-0003).
    """
    repository = request.app.state.ml_repository

    # Authenticated tenant resolution (user JWT or internal service token).
    company_id = await _resolve_company_id(request, request_data)

    # Get model metadata from database
    model_data = await repository.get_model_by_id(
        company_id=company_id,
        model_id=request_data.model_id
    )

    if not model_data:
        raise HTTPException(status_code=404, detail="Model not found")

    # Check if model is deployed
    if model_data.get('status') not in ['production', 'active']:
        raise HTTPException(
            status_code=400,
            detail=f"Model status is '{model_data.get('status')}'. Only 'production' or 'active' models can be used for inference."
        )

    mlflow_run_id = model_data.get('mlflow_run_id')
    if not mlflow_run_id:
        raise HTTPException(
            status_code=400,
            detail="Model has no MLflow run_id. Cannot load model."
        )

    try:
        # Load model from MLflow
        model = load_model_from_mlflow(mlflow_run_id)

        # Get feature engineering configuration
        feature_config_id = model_data.get('feature_config_id')
        if not feature_config_id:
            raise HTTPException(
                status_code=400,
                detail="Model has no feature_config_id. Cannot perform feature engineering."
            )

        # Load feature config from database
        feature_config = await repository.get_feature_config_by_id(
            company_id=company_id,
            config_id=feature_config_id
        )

        if not feature_config:
            raise HTTPException(
                status_code=404,
                detail=f"Feature config {feature_config_id} not found"
            )

        # Get Feature Store from app state
        feature_store = getattr(request.app.state, 'feature_store', None)
        equipment_id = request_data.sensor_data.get('equipment_id')

        # Create feature engineering engine
        fe_engine = FeatureEngineeringEngine(
            feature_config=feature_config,
            feature_store=feature_store,
            equipment_id=equipment_id
        )

        # Transform sensor data to features (async)
        input_features = await fe_engine.transform(request_data.sensor_data)

        logger.info(f"Engineered {input_features.shape[1]} features from sensor data (Feature Store: {feature_store is not None})")

        # Score the features through the anomaly-detector registry (ADR-0010). The model
        # record may name a detector (a domain may register its own); the default 'sklearn'
        # built-in reproduces the platform's scikit-learn scoring.
        detector_name = model_data.get('detector', 'sklearn')
        detector = get_detector(detector_name)
        if detector is None:
            raise HTTPException(status_code=500, detail=f"Unknown anomaly detector: {detector_name}")

        result = await detector(
            input_features, model, request_data.threshold,
            DetectorContext(equipment_id=equipment_id),
        )
        anomaly_score = result.score
        is_anomaly = result.is_anomaly

        logger.info(f"Inference complete: model={request_data.model_id}, detector={detector_name}, score={anomaly_score:.4f}, anomaly={is_anomaly}")

        return InferenceResponse(
            model_id=request_data.model_id,
            prediction=anomaly_score,
            is_anomaly=is_anomaly,
            threshold=request_data.threshold,
            model_version=model_data.get('model_version')
        )

    except Exception as e:
        logger.error(f"Inference failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Inference failed: {str(e)}"
        )
