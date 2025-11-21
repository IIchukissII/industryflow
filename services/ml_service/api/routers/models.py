"""
Models Router
Endpoints for model registry and management from industryflow database
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging
import json

import auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/models", tags=["Models"])


# ============================================================================
# Pydantic Models
# ============================================================================

class ModelMetadata(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    company_id: str
    equipment_id: Optional[str] = None
    model_name: str
    model_version: str
    model_type: str
    model_path: Optional[str] = None
    status: str
    accuracy: Optional[float] = None
    precision_score: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None
    auc_roc: Optional[float] = None
    training_metrics: Optional[Dict[str, Any]] = None
    hyperparameters: Optional[Dict[str, Any]] = None
    feature_names: Optional[List[str]] = None
    sensor_ids: Optional[List[str]] = None
    training_samples: Optional[int] = None
    training_start_date: Optional[datetime] = None
    training_end_date: Optional[datetime] = None
    training_duration_seconds: Optional[int] = None
    mlflow_run_id: Optional[str] = None
    mlflow_experiment_id: Optional[str] = None
    created_at: datetime
    deployed_at: Optional[datetime] = None


class ModelListResponse(BaseModel):
    total: int
    models: List[ModelMetadata]


class ModelCreateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    equipment_id: Optional[str] = None
    model_name: str
    model_version: str
    model_type: str
    model_path: Optional[str] = None
    status: str = "training"
    accuracy: Optional[float] = None
    precision_score: Optional[float] = None
    recall: Optional[float] = None
    f1_score: Optional[float] = None
    auc_roc: Optional[float] = None
    training_metrics: Optional[Dict[str, Any]] = None
    hyperparameters: Optional[Dict[str, Any]] = None
    feature_names: Optional[List[str]] = None
    sensor_ids: Optional[List[str]] = None
    training_samples: Optional[int] = None
    training_start_date: Optional[datetime] = None
    training_end_date: Optional[datetime] = None
    training_duration_seconds: Optional[int] = None
    mlflow_run_id: Optional[str] = None
    mlflow_experiment_id: Optional[str] = None


class ModelDeployRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    model_id: str
    environment: str = Field(
        "production",
        description="Deployment environment: staging, production, archived, active"
    )


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
# API Endpoints
# ============================================================================

@router.post("", status_code=201)
async def create_model(
    model_data: ModelCreateRequest,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Register a new trained model
    Called after model training to register metadata
    """
    repository = request.app.state.ml_repository

    # Create model in database
    model_id = await repository.create_model(
        company_id=company_id,
        model_data=model_data.model_dump()
    )

    if not model_id:
        raise HTTPException(status_code=500, detail="Failed to create model")

    logger.info(f"Model registered: {model_id} - {model_data.model_name}")

    return {
        "status": "success",
        "model_id": str(model_id),
        "model_name": model_data.model_name,
        "created_at": datetime.now().isoformat()
    }


@router.get("", response_model=ModelListResponse)
async def list_models(
    request: Request,
    company_id: str = Depends(get_company_id_dependency),
    status: Optional[str] = None,
    limit: int = 50
):
    """
    List all trained models for the authenticated user's company
    Schema-per-tenant isolation
    """
    repository = request.app.state.ml_repository

    models_data = await repository.get_all_models(
        company_id=company_id,
        status=status,
        limit=limit
    )

    models = []
    for data in models_data:
        # Parse JSON fields if stored as strings
        if isinstance(data.get('training_metrics'), str):
            try:
                data['training_metrics'] = json.loads(data['training_metrics'])
            except:
                data['training_metrics'] = None

        if isinstance(data.get('hyperparameters'), str):
            try:
                data['hyperparameters'] = json.loads(data['hyperparameters'])
            except:
                data['hyperparameters'] = None

        models.append(ModelMetadata(**data))

    return ModelListResponse(total=len(models), models=models)


@router.get("/{model_id}")
async def get_model(
    model_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Get detailed information about a specific model"""
    repository = request.app.state.ml_repository
    
    model_data = await repository.get_model_by_id(
        company_id=company_id,
        model_id=model_id
    )
    
    if not model_data:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Parse JSON fields if stored as strings
    if isinstance(model_data.get('training_metrics'), str):
        try:
            model_data['training_metrics'] = json.loads(model_data['training_metrics'])
        except:
            model_data['training_metrics'] = None
    
    if isinstance(model_data.get('hyperparameters'), str):
        try:
            model_data['hyperparameters'] = json.loads(model_data['hyperparameters'])
        except:
            model_data['hyperparameters'] = None
    
    return model_data


@router.post("/{model_id}/deploy")
async def deploy_model(
    model_id: str,
    request_data: ModelDeployRequest,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Update model status (deploy/undeploy)"""
    valid_statuses = ['production', 'staging', 'archived', 'active']
    if request_data.environment not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid environment. Must be one of: {', '.join(valid_statuses)}"
        )
    
    repository = request.app.state.ml_repository
    
    # Verify model exists
    model_data = await repository.get_model_by_id(
        company_id=company_id,
        model_id=model_id
    )
    
    if not model_data:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Update status
    success = await repository.update_model_status(
        company_id=company_id,
        model_id=model_id,
        status=request_data.environment
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to update model status")
    
    logger.info(f"Model {model_id} status updated to {request_data.environment}")
    
    return {
        "status": "success",
        "model_id": model_id,
        "environment": request_data.environment,
        "updated_at": datetime.now().isoformat()
    }


@router.delete("/{model_id}")
async def delete_model(
    model_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Delete a model (sets status to archived)"""
    repository = request.app.state.ml_repository
    
    # Verify model exists
    model_data = await repository.get_model_by_id(
        company_id=company_id,
        model_id=model_id
    )
    
    if not model_data:
        raise HTTPException(status_code=404, detail="Model not found")
    
    # Archive model
    success = await repository.delete_model(
        company_id=company_id,
        model_id=model_id
    )
    
    if not success:
        raise HTTPException(status_code=500, detail="Failed to archive model")
    
    logger.info(f"Model {model_id} archived")
    
    return {
        "status": "archived",
        "model_id": model_id
    }


@router.get("/latest/{model_type}")
async def get_latest_model(
    model_type: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Get the latest active model for a model type"""
    repository = request.app.state.ml_repository
    
    model_data = await repository.get_latest_model(
        company_id=company_id,
        model_type=model_type
    )
    
    if not model_data:
        raise HTTPException(
            status_code=404,
            detail=f"No active model found for model type: {model_type}"
        )
    
    return model_data


@router.post("/compare")
async def compare_models(
    model_ids: List[str],
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Compare metrics across multiple models"""
    repository = request.app.state.ml_repository
    
    comparison = await repository.compare_models(
        company_id=company_id,
        model_ids=model_ids
    )
    
    return {
        "models_compared": len(comparison),
        "comparison": comparison
    }


@router.get("/{model_id}/download")
async def download_model(
    model_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Get download URL for a model (from MLflow/MinIO)"""
    repository = request.app.state.ml_repository
    
    model_data = await repository.get_model_by_id(
        company_id=company_id,
        model_id=model_id
    )
    
    if not model_data:
        raise HTTPException(status_code=404, detail="Model not found")
    
    return {
        "model_id": model_id,
        "model_name": model_data.get('model_name'),
        "model_path": model_data.get('model_path'),
        "download_info": "Model stored in MLflow/MinIO. Use MLflow client to download.",
        "mlflow_run_id": model_data.get('mlflow_run_id')
    }
