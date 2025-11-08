"""
ML Models Router
Schema-per-tenant architecture with JWT-based company_id
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import List, Optional
from pydantic import BaseModel
import asyncpg
import sys
from pathlib import Path

# Add parent directory to path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

from models import MLModel, MLModelCreate
from repository import MLModelRepository
from auth import get_company_id

router = APIRouter(prefix="/api/ml-models", tags=["ML Models"])


# Request model for MLflow registration
class MLflowModelRegister(BaseModel):
    mlflow_run_id: str
    model_name: str
    equipment_id: str
    description: Optional[str] = None


async def get_db_pool(request: Request):
    """Get database pool from app state"""
    db_pool = request.app.state.db_pool
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_pool


@router.get("", response_model=List[dict])
async def list_ml_models(
    company_id: str = Depends(get_company_id),
    status: Optional[str] = None,
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """List ML models for authenticated user's company"""
    repo = MLModelRepository(db_pool)
    models = await repo.get_all_models(company_id, status=status)
    return models


@router.get("/{model_id}", response_model=dict)
async def get_ml_model(
    model_id: str,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Get a specific ML model by ID"""
    repo = MLModelRepository(db_pool)
    model = await repo.get_model_by_id(model_id, company_id)
    
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    return model


@router.post("", response_model=dict, status_code=201)
async def create_ml_model(
    model: MLModelCreate,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Create a new ML model entry for authenticated user's company"""
    repo = MLModelRepository(db_pool)
    
    try:
        model_id = await repo.create_model(model, company_id)
        new_model = await repo.get_model_by_id(model_id, company_id)
        return new_model
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create model: {str(e)}")


@router.post("/train", status_code=202)
async def train_ml_model(
    model_config: dict,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Train a new ML model (placeholder for Phase 2d)"""
    return {
        "status": "accepted",
        "message": "ML model training is planned for Phase 2d implementation",
        "company_id": company_id,
        "config": model_config
    }


@router.post("/register-from-mlflow", status_code=201)
async def register_model_from_mlflow(
    data: MLflowModelRegister,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """
    Register a trained model from MLflow into the production database.
    
    This endpoint:
    1. Fetches model metadata from MLflow (metrics, params, artifacts)
    2. Creates entry in ml_models table
    3. Makes model available for alert rules
    """
    import mlflow
    import os
    from datetime import datetime
    from uuid import UUID
    from config import normalize_company_id_to_schema
    
    try:
        # Set MLflow tracking URI
        mlflow_uri = os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow:5000')
        mlflow.set_tracking_uri(mlflow_uri)
        
        # Get run details from MLflow
        client = mlflow.tracking.MlflowClient()
        run = client.get_run(data.mlflow_run_id)
        
        # Extract metrics
        metrics = run.data.metrics
        params = run.data.params
        tags = run.data.tags
        
        # Build model path (MLflow URI format)
        model_path = f"runs:/{data.mlflow_run_id}/model"
        
        # Determine model type from tags or params
        model_type_str = tags.get('model_type')
        if not model_type_str:
            if 'learning_rate' in params or 'max_depth' in params:
                model_type_str = 'xgboost'
            elif 'n_estimators' in params and 'contamination' in params:
                model_type_str = 'isolation_forest'
            elif 'n_estimators' in params:
                model_type_str = 'random_forest'
            else:
                model_type_str = 'statistical'
        
        print(f"Detected model type: {model_type_str} from run {data.mlflow_run_id[:8]}", flush=True)
        
        # Auto-increment version for models with same name
        schema_name = normalize_company_id_to_schema(company_id)
        async with db_pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            max_version = await conn.fetchval(
                """
                SELECT COALESCE(MAX(version), 0) 
                FROM ml_models 
                WHERE name = $1
                """,
                data.model_name
            )
            next_version = max_version + 1
        
        model_data = MLModelCreate(
            name=data.model_name,
            description=data.description or f"Model registered from MLflow run {data.mlflow_run_id[:8]}",
            model_type=model_type_str,
            model_path=model_path,
            model_metadata={
                'mlflow_run_id': data.mlflow_run_id,
                'mlflow_experiment_id': str(run.info.experiment_id),
                'equipment_id': data.equipment_id,
                'params': params,
                'tags': dict(tags)
            },
            training_dataset=f"{company_id}/{data.equipment_id}",
            training_date=datetime.fromtimestamp(run.info.start_time / 1000),
            training_duration_seconds=int((run.info.end_time - run.info.start_time) / 1000) if run.info.end_time else None,
            accuracy=metrics.get('test_accuracy'),
            precision_score=metrics.get('test_precision'),
            recall=metrics.get('test_recall'),
            f1_score=metrics.get('test_f1'),
            status='active',
            version=next_version,
        )
        
        # Save to database
        repo = MLModelRepository(db_pool)
        model_id = await repo.create_model(model_data, company_id)
        
        # Get the created model
        new_model = await repo.get_model_by_id(model_id, company_id)
        
        return new_model
        
    except Exception as e:
        raise HTTPException(
            status_code=400, 
            detail=f"Failed to register model from MLflow: {str(e)}"
        )


@router.put("/{model_id}/status", status_code=200)
async def update_model_status(
    model_id: str,
    status: str,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """
    Update model status (e.g., active → production)
    
    Valid statuses: active, production, deprecated, archived
    """
    from config import normalize_company_id_to_schema
    
    repo = MLModelRepository(db_pool)
    
    # Get model and verify it exists
    model = await repo.get_model_by_id(model_id, company_id)
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Update status
    try:
        schema_name = normalize_company_id_to_schema(company_id)
        async with db_pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            # If setting to production, demote other production models for same equipment
            if status == 'production':
                import json
                metadata = json.loads(model.get('model_metadata', '{}')) if isinstance(model.get('model_metadata'), str) else model.get('model_metadata', {})
                equipment_id = metadata.get('equipment_id')
                if equipment_id:
                    await conn.execute(
                        """
                        UPDATE ml_models 
                        SET status = 'active'
                        WHERE model_metadata->>'equipment_id' = $1
                          AND status = 'production'
                          AND model_id != $2
                        """,
                        equipment_id,
                        model_id
                    )
            
            # Update this model's status
            await conn.execute(
                "UPDATE ml_models SET status = $1 WHERE model_id = $2",
                status,
                model_id
            )
        
        # Return updated model
        updated_model = await repo.get_model_by_id(model_id, company_id)
        return updated_model
        
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to update model status: {str(e)}"
        )
