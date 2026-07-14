# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Inference Router
Real-time ML inference endpoint for anomaly detection in sensor data
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from prometheus_client import Counter
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import logging
import mlflow
import mlflow.pyfunc
import os
import hmac
import uuid

import json

import auth
import model_cache
from feature_engineering import FeatureEngineeringEngine
from extensions import get_detector, DetectorContext, UninterpretableModel
from model_uri import resolve_model_uri

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/inference", tags=["Inference"])

MLFLOW_TRACKING_URI = os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow:5000')
mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)

# Degraded serving must be measurable, not just loggable (ADR-0024 dec 5 / ADR-0016): an operator
# needs to see that scores are being produced on neutralized input, and to alert on it if the
# switch is left off. Counts feature *slots* neutralized, so it rises with both traffic and the
# number of stateful features in play.
STATEFUL_NEUTRALIZED = Counter(
    "ml_stateful_features_neutralized_total",
    "Feature slots filled with a neutral value because the stateful-feature kill-switch is off",
)


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
    degraded: bool = Field(
        default=False,
        description="True when the stateful-feature kill-switch (ADR-0024) is off and this score "
                    "was computed with those features neutralized — the score is real but the "
                    "input was partially degraded. Consumers should treat it as lower-confidence.",
    )
    neutralized_features: Optional[List[str]] = Field(
        default=None, description="Feature slots filled with a neutral value instead of computed.",
    )


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

    # Otherwise require a user JWT — from the Bearer header (API/service clients) or the
    # httpOnly if_access cookie (same-origin browser calls behind the TLS edge, e.g. the Models
    # drift panel). Pass BOTH explicitly: verify_jwt_token is written as a FastAPI dependency, so
    # its if_access default is a Cookie(None) marker (truthy) — calling it positionally would make
    # the cookie branch fire on a FieldInfo and 500 instead of cleanly reading the cookie / 401ing.
    user_data = await auth.verify_jwt_token(
        authorization=request.headers.get("Authorization"),
        if_access=request.cookies.get("if_access"),
    )
    claim = user_data.get("payload", {}).get("company_id")
    if claim:
        return str(claim)
    return await _company_id_from_user(request, user_data["user_id"])


# ============================================================================
# Helper Functions
# ============================================================================

def _load_model_uncached(mlflow_run_id: str):
    """Cold-load a model from MLflow (the slow path behind the warm cache).

    The URI is RESOLVED, not assumed (#240). MLflow 3 stores a logged model under
    `<experiment>/models/m-<id>/`, not under its run — so `runs:/<run_id>/model` addresses a path that
    does not exist on a real deployment, and the artifact proxy 404s until the client gives up. It
    only ever "worked" against a local artifact directory, which is what CI and every local test used.
    """
    model_uri = resolve_model_uri(mlflow_run_id)
    model = mlflow.pyfunc.load_model(model_uri)
    logger.info(f"Model loaded from MLflow: {model_uri}")
    return model


def load_model_from_mlflow(mlflow_run_id: str):
    """Load a model by run_id, warm from the process-local cache when already loaded.

    The anomaly detector and the drift evaluator both score with the same models
    repeatedly; the cache avoids a cold MLflow fetch on every call (see model_cache).
    """
    try:
        return model_cache.get_or_load(mlflow_run_id, _load_model_uncached)
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

        # Baseline provider (Spark-materialized aggregates) from app state (ADR-0023)
        baseline_provider = getattr(request.app.state, 'baseline_provider', None)
        equipment_id = request_data.sensor_data.get('equipment_id')

        # Create feature engineering engine
        fe_engine = FeatureEngineeringEngine(
            feature_config=feature_config,
            baseline_provider=baseline_provider,
            equipment_id=equipment_id,
            company_id=company_id,
            kill_switch=getattr(request.app.state, 'stateful_switch', None),
        )

        # Transform sensor data to features (async)
        input_features = await fe_engine.transform(request_data.sensor_data)

        logger.info(f"Engineered {input_features.shape[1]} features from sensor data (baseline provider: {baseline_provider is not None})")

        # A score computed on neutralized input must not be indistinguishable from an ordinary one
        # (ADR-0024 dec 5): trading a loud failure for a silent, confidently-wrong score is the one
        # outcome the kill-switch must not buy. Surface it on the response and in the metric.
        neutralized = fe_engine.neutralized_features
        if neutralized:
            STATEFUL_NEUTRALIZED.inc(len(neutralized))

        # Score the features through the anomaly-detector registry (ADR-0010). The model
        # record may name a detector (a domain may register its own); the default 'sklearn'
        # built-in reproduces the platform's scikit-learn scoring.
        detector_name = model_data.get('detector', 'sklearn')
        detector = get_detector(detector_name)
        if detector is None:
            raise HTTPException(status_code=500, detail=f"Unknown anomaly detector: {detector_name}")

        # An autoencoder's error is unbounded, so it is scored against the error the model itself saw
        # in training (ADR-0028). Where that scale lives is deliberately not fixed by the ADR; today it
        # rides in training_metrics, which needs no migration.
        training_metrics = model_data.get('training_metrics') or {}
        if isinstance(training_metrics, str):
            training_metrics = json.loads(training_metrics)

        try:
            result = await detector(
                input_features, model, request_data.threshold,
                DetectorContext(
                    equipment_id=equipment_id,
                    reconstruction_scale=training_metrics.get('reconstruction_scale'),
                ),
            )
        except UninterpretableModel as exc:
            # ADR-0028 dec 2: the detector could not establish what this model's output MEANS, so it
            # declined to score it. That is a refusal, not a crash — and it must never be resolved by
            # returning a number anyway. A wrong score is worse than none: ADR-0021 alerts on these,
            # and ADR-0022 asks an operator to label the alerts.
            logger.warning(f"Refusing to score model {request_data.model_id}: {exc}")
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (
                        "This model's output cannot be interpreted as an anomaly score, so it will "
                        "not be scored. Returning a guess would risk alerting on a number nobody "
                        "can justify (ADR-0028)."
                    ),
                    "detector": detector_name,
                    "reason": str(exc),
                },
            ) from exc

        anomaly_score = result.score
        is_anomaly = result.is_anomaly

        logger.info(f"Inference complete: model={request_data.model_id}, detector={detector_name}, score={anomaly_score:.4f}, anomaly={is_anomaly}")

        return InferenceResponse(
            model_id=request_data.model_id,
            prediction=anomaly_score,
            is_anomaly=is_anomaly,
            threshold=request_data.threshold,
            model_version=model_data.get('model_version'),
            degraded=bool(neutralized),
            neutralized_features=neutralized or None,
        )

    except Exception as e:
        logger.error(f"Inference failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Inference failed: {str(e)}"
        )
