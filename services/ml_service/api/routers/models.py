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

import asyncpg

import auth
import config
import extensions
import load_probe
import model_compat
import upload_capability
import uploaded_model

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
    # The read path already SELECTs `description` and _format_model_row surfaces it; without a field
    # here the response model drops it at the door — the same silent loss provenance suffered. It is
    # load-bearing for an uploaded model, whose author writes a description the operator should read.
    description: Optional[str] = None
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
    # ADR-0030 dec 8. Provenance is recorded as a first-class fact so an operator can SEE where a
    # model came from — a fact nobody can read is not one the platform holds. This is a response
    # model, so a field it does not declare is dropped at the door however faithfully the layers
    # beneath carried it.
    #
    # None means "predates the distinction", which is not "unknown origin": no model registered
    # before that migration can be an upload, because there was no way to upload one.
    provenance: Optional[str] = None
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
    # An externally-authored model (ADR-0030): where its artifact was put, and what its author says
    # its output means. Both absent for a kernel-authored model — its run answers the first, and its
    # detector already declares the second (ADR-0028 dec 1).
    #
    # There is deliberately no `provenance` field. It is the one fact most worth lying about, and the
    # platform reads it off how the model arrived rather than asking (ADR-0030 dec 8).
    artifact_uri: Optional[str] = None
    score_semantics: Optional[str] = None
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
    artifact_uri: Optional[str] = None,
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

    An artifact is addressed by its run, or — for one the platform never watched being made — by
    where it was put (ADR-0030 dec 8: no synthetic run, so nothing else answers this). The comparison
    itself is unchanged and deliberately so: it reads the declarations off the artifact and compares
    them to what this image installs, and it does not care who made it. What differs for an uploaded
    artifact is the *standing of its input* — declared by a stranger rather than recorded by
    something that watched — which is why more is asked of it elsewhere at this same gate, not here.

    Returns None only when there is nothing to evaluate: no run AND no artifact. Such a model is not
    refused; it is simply unjudged. An uploaded model can never reach that state — an upload with no
    artifact is not an upload.
    """
    if not mlflow_run_id and not artifact_uri:
        return None

    try:
        if mlflow_run_id:
            compatibility = await model_compat.evaluate_run(mlflow_run_id)
        else:
            compatibility = await model_compat.evaluate_uri(artifact_uri)
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

@router.post("/upload-capability", status_code=201)
async def mint_upload_capability(
    request: Request,
    current_user: dict = Depends(auth.verify_jwt_token),
    company_id: str = Depends(get_company_id_dependency),
):
    """Mint a short-lived capability for uploading one externally-authored model (ADR-0030 dec 3).

    This is the platform session becoming an upload — *derived from* a session, never *presented as*
    one. The mediator that holds the artifact-store credential resolves the opaque handle and learns
    nothing else; teaching it to verify sessions instead would let a compromise there forge any
    user's session in any tenant, since the signature is symmetric (ADR-0015 dec 7, dec 4 rev 1).

    Minted here because this service already establishes the tenant from a verified principal for its
    own reasons (ADR-0003 dec 1), and is the authority that will judge the artifact at the
    registration gate — so the authority that will judge it is the one that authorises it to arrive.
    """
    store = getattr(request.app.state, "capability_store", None)
    if store is None:
        raise HTTPException(
            status_code=501,
            detail=(
                "This deployment has no capability store configured, so it cannot mint upload "
                "capabilities and does not accept externally-authored models."
            ),
        )

    user = current_user.get("payload", {}).get("sub") or str(current_user.get("user_id", ""))
    try:
        # `build_record` is the decision — what this handle is bound to — and it refuses rather than
        # improvise when there is no verified principal to bind to. The write is here because it is
        # I/O and this store is async; `mint` is the same decision for a synchronous store.
        handle, record = upload_capability.build_record(user=user, company_id=company_id)
    except ValueError as exc:
        # No tenant, or no user. A handle bound to nothing is not a weaker capability; it is a hole.
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await store.set(
        upload_capability.store_key(handle),
        json.dumps(record),
        ex=upload_capability.DEFAULT_TTL_SECONDS,
    )

    logger.info(f"Minted an upload capability for {user} (tenant {company_id})")
    return {
        "capability": handle,
        "expires_in": upload_capability.DEFAULT_TTL_SECONDS,
        "upload_url": config.config.UPLOAD_GATEWAY_URL or None,
    }


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

    # Read where it came from; never ask (ADR-0030 dec 8).
    provenance = uploaded_model.provenance_of(
        mlflow_run_id=payload.get("mlflow_run_id"),
        artifact_uri=payload.get("artifact_uri"),
    )
    payload["provenance"] = provenance

    if provenance == uploaded_model.PROVENANCE_UPLOADED and not payload.get("artifact_uri"):
        # Neither a run nor an artifact: there is nothing to judge, and ADR-0030's whole point is
        # that an unwatched model is judged rather than trusted. Refuse instead of registering
        # something no gate can ever have an opinion about.
        raise HTTPException(
            status_code=422,
            detail=(
                "A model must either be logged through the platform (which records its run) or "
                "carry the artifact it was uploaded as. This one has neither, so nothing can "
                "establish what it is (ADR-0030)."
            ),
        )

    compatibility = await _evaluate_or_reject(
        payload.get("mlflow_run_id"),
        on_incompatible=422,
        model_label=model_data.model_name,
        artifact_uri=payload.get("artifact_uri"),
    )
    if compatibility:
        payload["compatibility_status"] = compatibility.status
        payload["compatibility_detail"] = compatibility.as_detail()
        payload["compatibility_checked_at"] = datetime.now(timezone.utc)

    # What an uploaded model must answer and a kernel-authored one already has (ADR-0030 dec 5).
    # Asked here — at the gate that already refuses what this environment cannot honour — because
    # only the serving side holds the vocabulary and the live detector registry, and that registry is
    # discovered rather than hand-kept.
    if provenance == uploaded_model.PROVENANCE_UPLOADED:
        verdict = uploaded_model.judge_semantics(
            payload.get("score_semantics"),
            compatibility.flavor if compatibility else None,
            vocabulary=extensions.SCORE_SEMANTICS,
            detectors_for=extensions.detectors_for,
        )
        if verdict.refused:
            logger.warning(f"Refusing uploaded model '{model_data.model_name}': {verdict.reason}")
            raise HTTPException(status_code=422, detail={
                "message": verdict.reason,
                "declared_semantics": payload.get("score_semantics"),
                "flavor": compatibility.flavor if compatibility else None,
            })
        logger.info(
            f"Uploaded model '{model_data.model_name}' declares '{payload.get('score_semantics')}'; "
            f"scored here by '{verdict.detector}'"
        )

    payload.pop("score_semantics", None)  # judged, not stored: the detector remains the authority
    # on what an output means (ADR-0028 dec 1), and a second copy could only drift from it.

    # Create model in database
    try:
        model_id = await repository.create_model(
            company_id=company_id,
            model_data=payload
        )
    except asyncpg.UniqueViolationError:
        # Already registered under this name and version. A 409 says what happened; the 500 this
        # used to return told the caller to retry something that can never succeed.
        raise HTTPException(
            status_code=409,
            detail=(
                f"A model named '{model_data.model_name}' version {model_data.model_version} is "
                f"already registered. Register a new version rather than replacing one: a model "
                f"version is a record of what was served, and overwriting it would erase that."
            ),
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
        model_label = str(model_data.get("model_name", model_id))
        compatibility = await _evaluate_or_reject(
            model_data.get("mlflow_run_id"),
            on_incompatible=409,
            model_label=model_label,
            # An uploaded model is addressed by where its artifact is, because it has no run
            # (ADR-0030 dec 8). Without this it would reach this gate unjudged — the same hole the
            # registration gate had, in the place where it would actually matter.
            artifact_uri=model_data.get("artifact_uri"),
        )
        if compatibility:
            await repository.update_model_compatibility(
                company_id=company_id,
                model_id=model_id,
                compatibility=compatibility,
            )

        # ADR-0030 dec 7 — the empirical check, for an artifact nothing watched being made.
        #
        # ADR-0027 dec 5 makes the empirical check the AUTHORITY over the declarative one, and it is
        # a round-trip: a model trained by the real authoring image must load and score in the real
        # serving image. An uploaded artifact has no authoring image, so one end of that round-trip
        # does not exist. It is replaced rather than dropped — dropping it would leave this model
        # judged only by assertions checked against assertions, which is what dec 5 rejects.
        #
        # Here rather than at registration because this is where the answer has to be true: the
        # verdict expires, and this is the gate that already knows that. It EXTENDS this gate rather
        # than adding a third — a parallel path would be a second answer to a settled question.
        if uploaded_model.provenance_of(
            mlflow_run_id=model_data.get("mlflow_run_id"),
            artifact_uri=model_data.get("artifact_uri"),
        ) == uploaded_model.PROVENANCE_UPLOADED:
            result = await load_probe.probe(model_data["artifact_uri"])
            if result.refused:
                logger.warning(f"Refusing to deploy uploaded model '{model_label}': {result.error}")
                raise HTTPException(status_code=409, detail={
                    "message": (
                        "This model has not been shown to load and score in this serving "
                        "environment, so it will not be deployed (ADR-0030 dec 7). Nothing watched "
                        "it being made, so what it declares is an assertion — the one thing that "
                        "can still be established is whether it works here, and it did not."
                    ),
                    "stage": result.stage,
                    "error": result.error,
                })
            logger.info(f"Uploaded model '{model_label}' loaded and scored in this environment")

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
