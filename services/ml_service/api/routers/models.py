# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Models Router
Endpoints for model registry and management from industryflow database
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
import logging
import json

import auth
import model_compat

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
    equipment_type: Optional[str] = None
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
    feature_config_id: Optional[str] = None
    sensor_ids: Optional[List[str]] = None
    training_samples: Optional[int] = None
    training_start_date: Optional[datetime] = None
    training_end_date: Optional[datetime] = None
    training_duration_seconds: Optional[int] = None
    mlflow_run_id: Optional[str] = None
    mlflow_experiment_id: Optional[str] = None
    # ADR-0027 dec 7: whether this serving environment still satisfies what the model declares.
    # Surfaced on the model, never fired as an alert — a version mismatch is a mechanical fact about
    # two containers, not the statistical claim about the world that ADR-0022's lane carries.
    # None means "never evaluated" (registered before ADR-0027), which is not the same as "fine".
    compatibility_status: Optional[str] = None
    compatibility_detail: Optional[Dict[str, Any]] = None
    compatibility_checked_at: Optional[datetime] = None
    created_at: datetime
    deployed_at: Optional[datetime] = None


class ModelListResponse(BaseModel):
    total: int
    models: List[ModelMetadata]


class ModelCreateRequest(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    equipment_id: Optional[str] = None
    equipment_type: Optional[str] = None
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
    feature_config_id: Optional[str] = None
    sensor_ids: Optional[List[str]] = None
    training_samples: Optional[int] = None
    training_start_date: Optional[datetime] = None
    training_end_date: Optional[datetime] = None
    training_duration_seconds: Optional[int] = None
    mlflow_run_id: Optional[str] = None
    mlflow_experiment_id: Optional[str] = None
    # ADR-0021: compact training-window distribution snapshot the drift evaluator
    # compares recent data against. Absent → the model reports "drift unavailable".
    reference_profile: Optional[Dict[str, Any]] = None


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
# ADR-0027 — the compatibility gate
# ============================================================================

async def _evaluate_or_reject(
    mlflow_run_id: Optional[str],
    *,
    on_incompatible: int,
    model_label: str,
) -> Optional[model_compat.Compatibility]:
    """Evaluate the artifact against this environment, and refuse it if we cannot honour it.

    One comparison, called at the two gates that admit a model, with different status codes because
    they mean different things (ADR-0027 dec 5):

      422 at registration — the artifact was never servable here. The author is present; they can fix
          their environment and log again.
      409 at deployment  — it was servable when it was registered, and the ground moved underneath it
          (this image was rebuilt). Nothing about the model changed; the world did.

    The refusal lives at these gates and NOT in `predict`: re-checking on every request would buy
    nothing these two gates and the CI round-trip have not already established, and would put artifact
    IO on the hot path.

    Returns None when there is nothing to evaluate — a model with no run has no artifact and is not on
    the path at all. It is not refused; it is simply unjudged.
    """
    if not mlflow_run_id:
        return None

    try:
        compatibility = await model_compat.evaluate_run(mlflow_run_id)
    except model_compat.ArtifactUnreadable as exc:
        # We could not READ the declarations, so we have no verdict — and saying "your model is
        # broken" because the tracking store is down would be a lie with a permanent-sounding status
        # code. 503: transient, retry. ADR-0027's rule is that we refuse what we cannot honour; it is
        # not a licence to guess.
        logger.warning(f"Compatibility undecidable for run {mlflow_run_id}: {exc}")
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not read the model artifact's declared requirements from the tracking store, "
                "so its compatibility with this serving environment cannot be established. This is "
                "transient — retry."
            ),
        ) from exc

    if not compatibility.servable:
        logger.warning(
            f"Refusing model '{model_label}' (run {mlflow_run_id}): {compatibility.reasons}"
        )
        raise HTTPException(
            status_code=on_incompatible,
            detail={
                "message": (
                    "This serving environment cannot honour what the model declares, so it will not "
                    "be served (ADR-0027). Loading it anyway risks scoring silently wrong, which is "
                    "worse than refusing it."
                ),
                "reasons": compatibility.reasons,
                "flavor": compatibility.flavor,
                "declared": compatibility.declared,
                "serving": compatibility.present,
            },
        )

    if compatibility.status == model_compat.PATCH_DRIFT:
        logger.info(
            f"Model '{model_label}' (run {mlflow_run_id}) is servable with patch drift: "
            f"{compatibility.reasons}"
        )

    return compatibility


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

    ADR-0027: the artifact declares its requirements and this environment satisfies them or refuses
    the model. This is the FIRST of the two gates that admit a model — the earliest possible signal,
    while the author is still at their notebook and the run is fresh. The second is deployment
    (below), because this verdict expires: the serving image can be rebuilt underneath a model that
    is sitting unpromoted.
    """
    repository = request.app.state.ml_repository

    payload = model_data.model_dump()
    compatibility = await _evaluate_or_reject(
        payload.get("mlflow_run_id"),
        on_incompatible=422,
        model_label=model_data.model_name,
    )
    if compatibility:
        payload["compatibility_status"] = compatibility.status
        payload["compatibility_detail"] = compatibility.as_detail()
        payload["compatibility_checked_at"] = datetime.now(timezone.utc)

    # Create model in database
    model_id = await repository.create_model(
        company_id=company_id,
        model_data=payload
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

    # ADR-0027 dec 5 — the SECOND gate, and the one that actually protects what gets served.
    #
    # A model can sit unpromoted for weeks while this image is rebuilt underneath it, so the verdict
    # taken at registration EXPIRES. Only this gate is positioned to know that. It is also where a
    # model registered BEFORE ADR-0027 (verdict NULL — never evaluated) finally gets judged: not
    # evicted from a serving path it already occupies (dec 10), but barred from being re-deployed into
    # one if this environment can no longer honour it.
    #
    # Undeploying is never blocked: archiving a model is the remedy for a bad one, not another way to
    # break it.
    if request_data.environment in ("production", "active", "staging"):
        compatibility = await _evaluate_or_reject(
            model_data.get("mlflow_run_id"),
            on_incompatible=409,
            model_label=str(model_data.get("model_name", model_id)),
        )
        if compatibility:
            await repository.update_model_compatibility(
                company_id=company_id,
                model_id=model_id,
                compatibility=compatibility,
            )

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
