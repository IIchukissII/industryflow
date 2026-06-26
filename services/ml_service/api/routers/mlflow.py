# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
MLflow Router
Endpoints for MLflow experiment tracking with schema-per-tenant isolation
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
import logging
import os

import auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/mlflow", tags=["MLflow"])

MLFLOW_TRACKING_URI = os.getenv('MLFLOW_TRACKING_URI', 'http://mlflow:5000')


# ============================================================================
# Pydantic Models
# ============================================================================

class ExperimentResponse(BaseModel):
    experiment_id: str
    name: str
    artifact_location: str
    lifecycle_stage: str


class RunResponse(BaseModel):
    run_id: str
    experiment_id: str
    status: str
    start_time: int
    end_time: Optional[int]


class ModelResponse(BaseModel):
    name: str
    creation_timestamp: int
    last_updated_timestamp: int
    description: Optional[str]


# ============================================================================
# Dependency Injection
# ============================================================================

async def get_company_id_dependency(
    request: Request,
    current_user: dict = Depends(auth.verify_jwt_token)
) -> str:
    """Get company_id, preferring the JWT claim over a tenant-schema scan."""
    # Prefer the company_id claim from the JWT (ADR-0003 dec 2); avoids the per-request
    # tenant-schema scan below (review finding X2). Fall back to the scan for legacy tokens.
    claim = current_user.get("payload", {}).get("company_id")
    if claim:
        return str(claim)

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

@router.get("/experiments")
async def list_experiments(
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    List all MLflow experiments for authenticated user's company
    Queries mlflow database with schema-per-tenant isolation
    """
    repository = request.app.state.mlflow_repository
    
    experiments = await repository.get_all_experiments(company_id=company_id)
    
    return {
        "total": len(experiments),
        "experiments": experiments
    }


@router.get("/experiments/{experiment_id}")
async def get_experiment(
    experiment_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Get experiment details with schema-per-tenant filtering"""
    repository = request.app.state.mlflow_repository
    
    experiment = await repository.get_experiment_by_id(
        company_id=company_id,
        experiment_id=experiment_id
    )
    
    if not experiment:
        raise HTTPException(status_code=404, detail="Experiment not found")
    
    return experiment


@router.get("/experiments/{experiment_id}/runs")
async def list_runs(
    experiment_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency),
    max_results: int = 100
):
    """List runs for an experiment with schema-per-tenant filtering"""
    repository = request.app.state.mlflow_repository
    
    runs = await repository.get_runs_for_experiment(
        company_id=company_id,
        experiment_id=experiment_id,
        max_results=max_results
    )
    
    if not runs:
        # Check if experiment exists
        experiment = await repository.get_experiment_by_id(
            company_id=company_id,
            experiment_id=experiment_id
        )
        if not experiment:
            raise HTTPException(status_code=404, detail="Experiment not found")
    
    return {
        "total": len(runs),
        "runs": runs
    }


@router.get("/runs/{run_id}")
async def get_run(
    run_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Get run details with metrics, params, tags"""
    repository = request.app.state.mlflow_repository
    
    run_details = await repository.get_run_details(
        company_id=company_id,
        run_id=run_id
    )
    
    if not run_details:
        raise HTTPException(status_code=404, detail="Run not found")
    
    return run_details


@router.get("/runs/{run_id}/metrics")
async def get_run_metrics(
    run_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Get all metrics for a run with schema-per-tenant filtering"""
    repository = request.app.state.mlflow_repository
    
    run_details = await repository.get_run_details(
        company_id=company_id,
        run_id=run_id
    )
    
    if not run_details:
        raise HTTPException(status_code=404, detail="Run not found")
    
    return {
        "run_id": run_id,
        "metrics": run_details.get('metrics', {})
    }


@router.get("/models")
async def list_registered_models(
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """List all registered models with schema-per-tenant filtering"""
    repository = request.app.state.mlflow_repository
    
    models = await repository.get_registered_models(company_id=company_id)
    
    return {
        "total": len(models),
        "models": models
    }


@router.get("/models/{model_name}")
async def get_registered_model(
    model_name: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Get registered model details with versions"""
    repository = request.app.state.mlflow_repository
    
    model = await repository.get_registered_model_with_versions(
        company_id=company_id,
        model_name=model_name
    )
    
    if not model:
        raise HTTPException(status_code=404, detail="Model not found")
    
    return model


@router.get("/ui")
async def get_mlflow_ui_url():
    """Get MLflow UI URL (admin only - not tenant-facing)"""
    return {
        "ui_url": MLFLOW_TRACKING_URI,
        "message": "MLflow UI is for administrators only. Users access experiments via API."
    }


@router.get("/health")
async def mlflow_health(request: Request):
    """Check MLflow database connectivity"""
    try:
        pool = request.app.state.mlflow_pool
        
        async with pool.acquire() as conn:
            count = await conn.fetchval("SELECT COUNT(*) FROM public.experiments")
            
            return {
                "status": "healthy",
                "database": "mlflow",
                "experiments_total": count
            }
    except Exception as e:
        logger.error(f"MLflow health check failed: {e}")
        return {
            "status": "unhealthy",
            "error": str(e)
        }
