"""
Inference Router
Real-time ML inference endpoint for anomaly detection in sensor data
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import logging
import mlflow
import mlflow.pyfunc
import numpy as np
import os

import auth
# TODO: Feature engineering will be implemented in Phase 2-3
# This will use the flexible, configuration-driven feature engineering system
# from ..feature_engineering import engineer_features
# For now, feature engineering is not available

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

async def get_company_id_dependency(
    request: Request,
    current_user: dict = Depends(auth.verify_jwt_token)
) -> str:
    """Get company_id with database pool from app state"""
    db_pool = request.app.state.db_pool
    user_id = current_user["user_id"]

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
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Run ML inference on sensor data
    Returns anomaly score and whether it exceeds threshold
    """
    repository = request.app.state.ml_repository

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

        # TODO: Phase 2-3 - Implement flexible feature engineering
        # This will:
        # 1. Get feature config from database for the equipment type
        # 2. Read recent sensor readings from Feature Store
        # 3. Apply configured transformations (interaction, polynomial, statistical, etc.)
        # 4. Return engineered features in the order expected by the model

        raise HTTPException(
            status_code=501,
            detail="Feature engineering not yet implemented. "
                   "Phase 1 (Feature Store) is complete. "
                   "Phase 2-3 will implement flexible, configuration-driven feature engineering. "
                   "For now, ML inference requires manual feature engineering."
        )

        # Run prediction
        prediction = model.predict(input_features)

        # Extract anomaly score
        # For binary classifiers, prediction is array of class labels
        # For anomaly detectors, prediction might be anomaly score or -1/1
        if hasattr(model, 'predict_proba'):
            # Get probability of anomaly class (class 1)
            proba = model.predict_proba(input_features)
            anomaly_score = float(proba[0][1]) if proba.shape[1] > 1 else float(proba[0][0])
        elif isinstance(prediction[0], (int, np.integer)):
            # Binary prediction (-1 for anomaly in IsolationForest, 1 for normal)
            # Convert to 0-1 score
            if prediction[0] == -1:
                anomaly_score = 1.0  # High score = anomaly
            elif prediction[0] == 1:
                anomaly_score = 0.0  # Low score = normal
            else:
                anomaly_score = float(abs(prediction[0]))
        else:
            # Direct anomaly score
            anomaly_score = float(prediction[0])

        # Ensure score is between 0 and 1
        anomaly_score = max(0.0, min(1.0, anomaly_score))

        # Check if anomaly
        is_anomaly = anomaly_score >= request_data.threshold

        logger.info(f"Inference complete: model={request_data.model_id}, score={anomaly_score:.4f}, anomaly={is_anomaly}")

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


@router.post("/batch-predict")
async def batch_predict(
    model_id: str,
    sensor_data_list: List[Dict[str, Any]],
    request: Request,
    company_id: str = Depends(get_company_id_dependency),
    threshold: float = 0.85
):
    """
    Run ML inference on batch of sensor data
    More efficient for multiple predictions with same model
    """
    repository = request.app.state.ml_repository

    # Get model metadata
    model_data = await repository.get_model_by_id(
        company_id=company_id,
        model_id=model_id
    )

    if not model_data:
        raise HTTPException(status_code=404, detail="Model not found")

    if model_data.get('status') not in ['production', 'active']:
        raise HTTPException(
            status_code=400,
            detail=f"Model status is '{model_data.get('status')}'. Only 'production' or 'active' models can be used."
        )

    mlflow_run_id = model_data.get('mlflow_run_id')
    if not mlflow_run_id:
        raise HTTPException(status_code=400, detail="Model has no MLflow run_id")

    try:
        # TODO: Phase 2-3 - Implement flexible feature engineering for batch predictions
        raise HTTPException(
            status_code=501,
            detail="Batch inference not yet implemented. "
                   "Feature engineering system is being redesigned for flexibility. "
                   "Phase 1 (Feature Store) is complete. "
                   "Phase 2-3 will implement configuration-driven feature engineering."
        )

        # Batch prediction
        predictions = model.predict(batch_input)

        # Process predictions
        results = []
        for i, pred in enumerate(predictions):
            if hasattr(model, 'predict_proba'):
                proba = model.predict_proba(batch_input)
                anomaly_score = float(proba[i][1]) if proba.shape[1] > 1 else float(proba[i][0])
            elif isinstance(pred, (int, np.integer)):
                anomaly_score = 1.0 if pred == -1 else 0.0
            else:
                anomaly_score = float(pred)

            anomaly_score = max(0.0, min(1.0, anomaly_score))

            results.append({
                "index": i,
                "prediction": anomaly_score,
                "is_anomaly": anomaly_score >= threshold
            })

        return {
            "model_id": model_id,
            "total": len(results),
            "threshold": threshold,
            "results": results
        }

    except Exception as e:
        logger.error(f"Batch inference failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Batch inference failed: {str(e)}")
