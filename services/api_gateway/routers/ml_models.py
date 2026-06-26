# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
REST API endpoints for ML model management with schema-per-tenant routing
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Optional
from uuid import UUID
from pydantic import BaseModel
from enum import Enum

from dependencies import get_db_with_tenant, get_current_user_with_company
from models.user import User

router = APIRouter(prefix="/api/ml-models", tags=["ML Models"])


class ModelStatus(str, Enum):
    active = "active"
    deprecated = "deprecated"
    training = "training"
    failed = "failed"


class ModelType(str, Enum):
    isolation_forest = "isolation_forest"
    lstm = "lstm"
    autoencoder = "autoencoder"
    statistical = "statistical"


@router.get("")
async def list_ml_models(
        status: Optional[ModelStatus] = None,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """List all ML models for user's company (schema-routed)"""
    query = "SELECT * FROM ml_models WHERE 1=1"
    params = {}
    
    if status:
        query += " AND status = :status"
        params["status"] = status.value
    
    query += " ORDER BY created_at DESC"
    
    result = await db.execute(text(query), params)
    rows = result.fetchall()
    
    return [dict(row._mapping) for row in rows]


@router.get("/{model_id}")
async def get_ml_model(
        model_id: UUID,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Get single ML model by ID (schema-routed)"""
    result = await db.execute(
        text("SELECT * FROM ml_models WHERE model_id = :model_id"),
        {"model_id": model_id}
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="ML model not found")
    
    return dict(row._mapping)


@router.post("", status_code=201)
async def create_ml_model(
        model_data: dict,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Create new ML model entry for user's company (schema ensures tenant isolation)"""
    # Validate required fields
    if not model_data.get('model_name'):
        raise HTTPException(status_code=400, detail="model_name is required")
    
    params = {
        "company_id": str(current_user.company_id),
        "model_name": model_data.get('model_name'),
        "model_type": model_data.get('model_type', 'isolation_forest'),
        "model_version": model_data.get('model_version', 1),
        "mlflow_run_id": model_data.get('mlflow_run_id'),
        "sensor_pattern": model_data.get('sensor_pattern'),
        "training_metrics": model_data.get('training_metrics'),
        "hyperparameters": model_data.get('hyperparameters'),
        "feature_names": model_data.get('feature_names'),
        "model_path": model_data.get('model_path'),
        "status": model_data.get('status', 'training'),
        "description": model_data.get('description')
    }
    
    result = await db.execute(text("""
        INSERT INTO ml_models (
            company_id, model_name, model_type, model_version, mlflow_run_id,
            sensor_pattern, training_metrics, hyperparameters, feature_names,
            model_path, status, description
        ) VALUES (
            :company_id, :model_name, :model_type, :model_version, :mlflow_run_id,
            :sensor_pattern, :training_metrics, :hyperparameters, :feature_names,
            :model_path, :status, :description
        )
        RETURNING *
    """), params)
    
    row = result.fetchone()
    await db.commit()
    
    return {
        "message": "ML model created successfully",
        "model": dict(row._mapping)
    }


@router.put("/{model_id}")
async def update_ml_model(
        model_id: UUID,
        model_data: dict,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Update ML model (schema-routed)"""
    # Check if model exists
    check = await db.execute(
        text("SELECT 1 FROM ml_models WHERE model_id = :model_id"),
        {"model_id": model_id}
    )
    if not check.fetchone():
        raise HTTPException(status_code=404, detail="ML model not found")
    
    # Build update query
    updates = []
    params = {"model_id": model_id}
    
    for field in ['model_name', 'status', 'training_metrics', 'hyperparameters',
                  'model_path', 'description']:
        if field in model_data:
            updates.append(f"{field} = :{field}")
            params[field] = model_data[field]
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    query = f"""
        UPDATE ml_models 
        SET {', '.join(updates)}, updated_at = NOW() 
        WHERE model_id = :model_id 
        RETURNING *
    """
    
    result = await db.execute(text(query), params)
    row = result.fetchone()
    await db.commit()
    
    return {
        "message": "ML model updated successfully",
        "model": dict(row._mapping)
    }


@router.delete("/{model_id}")
async def delete_ml_model(
        model_id: UUID,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Delete ML model (schema-routed)"""
    result = await db.execute(
        text("DELETE FROM ml_models WHERE model_id = :model_id RETURNING *"),
        {"model_id": model_id}
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="ML model not found")
    
    await db.commit()
    
    return {
        "message": "ML model deleted successfully",
        "deleted_model": dict(row._mapping)
    }


@router.post("/train")
async def train_ml_model(
        model_name: str,
        sensor_pattern: Optional[str] = None,
        training_days: int = 7,
        current_user: User = Depends(get_current_user_with_company)
):
    """
    Train new ML model (placeholder for future implementation)
    
    Query Parameters:
    - model_name: Name for the new model
    - sensor_pattern: Pattern for sensors to include in training
    - training_days: Number of days of historical data to use
    """
    return {
        "message": "ML model training initiated",
        "status": "pending",
        "company_id": str(current_user.company_id),
        "model_name": model_name,
        "sensor_pattern": sensor_pattern,
        "training_days": training_days,
        "note": "Actual ML training will be implemented in future phase"
    }
