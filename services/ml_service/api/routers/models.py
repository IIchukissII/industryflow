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
import asyncio
import logging
import json

import asyncpg

import auth
import baseline_gate
import config
import extensions
import load_probe
import model_compat
import model_loader
import skops_trust
import upload_capability
import uploaded_model
import drift
from .inference import detector_for, load_model_or_refuse

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
    # Written at registration, SELECTed by both full-surface reads and surfaced by _format_model_row;
    # without a field here the response model would drop it at the door — the same silent loss
    # provenance suffered. It is load-bearing for an uploaded model, whose author writes a description
    # the operator should read.
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
    mlflow_run_id: Optional[str] = None
    mlflow_experiment_id: Optional[str] = None
    # ADR-0010 dec 6 / ADR-0028 dec 1: the detector that scores this model — the declaration of what
    # its output means. None only for an upload registered before the choice was recorded, which
    # serving refuses until it is registered again.
    detector: Optional[str] = None
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
    # What the model's drift baseline lets monitoring do, and why not where it cannot (ADR-0035 dec 4,
    # ADR-0030 dec 8: visibly un-monitorable).
    baseline: Optional[Dict[str, Any]] = None
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
    # What the model is for, in its author's words. Stored and shown on the model (ModelMetadata).
    description: Optional[str] = None
    model_version: str
    model_type: str
    model_path: Optional[str] = None
    # The detector that scores this model (ADR-0010 dec 6) — which is to say, what its output MEANS
    # (ADR-0028 dec 1). A kernel-authored model names it, defaulting to the `sklearn` compatibility
    # detector. An uploaded model need not: the gate picks the detector that implements its declared
    # semantics for its flavor, and refuses a named one that does not.
    detector: Optional[str] = None
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
        description="Deployment target: production or active (serve it), or deprecated (retire it)"
    )


# ============================================================================
# Dependency Injection
# ============================================================================

# The caller's tenant, read from the verified token's company_id claim (ADR-0003 dec 1, 2) — the
# one resolution every router here uses; there is no tenant-schema scan to fall back to.
get_company_id_dependency = auth.get_company_id


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


async def _admit_address_or_refuse(
    company_id: str, mlflow_run_id: Optional[str], artifact_uri: Optional[str], *, on_refused: int,
) -> None:
    """Refuse a model whose run or artifact location this tenant may not use (ADR-0003, ADR-0030).

    Both are caller-supplied, and everything after this point trusts them: the compatibility gate
    reads the artifact, the load probe executes its loader, and serving loads it with a credential
    that reaches every tenant's artifacts. So the address is held to the caller's tenant FIRST — a run
    in one of its experiments, or an upload its gateway admitted under its prefix — before any of that
    reads a byte. Asked again at deployment, for rows registered before this check existed.
    """
    try:
        await asyncio.to_thread(
            model_loader.check_admissible,
            company_id=company_id, mlflow_run_id=mlflow_run_id, artifact_uri=artifact_uri,
        )
    except model_loader.ModelNotAdmissible as exc:
        logger.warning(f"Refusing a model address for tenant {company_id}: {exc}")
        raise HTTPException(status_code=on_refused, detail={
            "message": "This model's artifact is not one your tenant may register or serve.",
            "reason": str(exc),
        }) from exc
    except model_loader.TrackingStoreUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "The tracking store did not answer, so it cannot be established that this run is "
                "your tenant's. This is transient — retry."
            ),
        ) from exc


async def _refuse_author_code_or_reject(
    mlflow_run_id: Optional[str], artifact_uri: Optional[str], *, on_refused: int,
) -> None:
    """Refuse a model whose load would run code its author chose, whatever its provenance (ADR-0037).

    Asked at registration and at deployment, before the compatibility gate reads the artifact and
    before the load probe runs, and again by every load. It reads the manifest only and imports
    nothing it names, so it runs here, in the serving process.
    """
    model_uri = artifact_uri
    try:
        model_uri = await asyncio.to_thread(
            model_loader.model_uri_for, mlflow_run_id=mlflow_run_id, artifact_uri=artifact_uri,
        )
        flavors = await asyncio.to_thread(skops_trust.read_flavors, model_uri)
    except Exception as exc:  # noqa: BLE001 — the store is outside our control; no verdict either way
        logger.warning(f"Could not read the manifest of {model_uri}: {exc}")
        raise HTTPException(
            status_code=503,
            detail=(
                "Could not read the model's manifest, so it cannot be established that "
                "loading it runs no code its author supplied. This is transient — retry."
            ),
        ) from exc
    try:
        skops_trust.refuse_author_code(flavors)
    except skops_trust.UnsafeArtifact as exc:
        logger.warning(f"Refusing model at {model_uri}: {exc}")
        raise HTTPException(status_code=on_refused, detail={
            "message": (
                "Loading this model would run code its author chose, which no model served here may "
                "do (ADR-0037). Log it in a data-only format: scikit-learn as skops, xgboost as its "
                "native booster."
            ),
            "reason": str(exc),
        }) from exc


def _kernel_detector_or_refuse(named: Optional[str], flavor: Optional[str]) -> str:
    """The detector a kernel-authored model is scored by, held to what the registry declares.

    `sklearn` when none is named — the compatibility detector every model before ADR-0028 names
    (ADR-0010 dec 6). A named detector must be loaded in this deployment, and if it declares the
    flavors it can score, the artifact's flavor must be one of them (ADR-0028 dec 4): otherwise the
    model would register and then be refused, or worse misread, at its first score.
    """
    name = named or "sklearn"
    capability = next((c for c in extensions.detector_capabilities() if c["name"] == name), None)
    if capability is None:
        raise HTTPException(status_code=422, detail={
            "message": f"No detector named '{name}' is loaded in this deployment.",
            "detectors": extensions.registered_detectors(),
        })
    flavors = capability.get("handles_flavors") or []
    if flavor and flavors and flavor not in flavors:
        raise HTTPException(status_code=422, detail={
            "message": (
                f"The '{name}' detector does not score `{flavor}` models, so nothing would know what "
                f"this model's output means (ADR-0028 dec 4). Name a detector that does."
            ),
            "flavor": flavor,
            "detector_flavors": flavors,
        })
    return name


# ============================================================================
# API Endpoints
# ============================================================================

@router.post("/upload-capability", status_code=201, dependencies=[Depends(auth.require_writer)])
async def mint_upload_capability(
    request: Request,
    current_user: dict = Depends(auth.verify_jwt_token),
    company_id: str = Depends(get_company_id_dependency),
):
    """Mint a short-lived capability for uploading one externally-authored model (ADR-0030 dec 3).

    This is the platform session becoming an upload — *derived from* a session, never *presented as*
    one. The mediator that holds the artifact-store credential resolves the opaque handle and learns
    nothing else; teaching it to verify sessions instead would let a compromise there forge any
    user's session in any tenant, since the signature is symmetric (ADR-0015 dec 7, dec 4).

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

    # A suspended company is issued nothing (ADR-0033 dec 3); the platform API keeps this marker
    # with the company's active flag, and every verifier refuses the company's handles by it.
    if await store.get(upload_capability.suspended_key(record["company_id"])) is not None:
        raise HTTPException(status_code=403, detail="This company is suspended.")

    # Index first: a handle that exists but is not indexed is one ending the user's sessions cannot
    # revoke. The index lives as long as its newest handle.
    index = upload_capability.user_index_key(record["user"])
    await store.sadd(index, handle)
    await store.expire(index, upload_capability.DEFAULT_TTL_SECONDS)
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


@router.post("", status_code=201, dependencies=[Depends(auth.require_writer)])
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
    return await register_model(model_data.model_dump(), company_id, request.app.state.ml_repository)


async def _bind_inputs_and_baseline(payload: Dict[str, Any], company_id: str, repository) -> Dict[str, Any]:
    """Bind the model's inputs and accept or refuse its feature sample; returns the baseline's state.

    The feature configuration must exist in the tenant. Bound to an equipment, the model reads its
    drift window from that equipment's sensors of the configuration's inputs, which must all exist. A
    sample is a baseline only when its columns are those inputs and it carries no scores (ADR-0035
    dec 2, 4); otherwise the model is registered without one and says why.
    """
    feature_config = None
    if payload.get("feature_config_id"):
        feature_config = await repository.get_feature_config_by_id(
            company_id=company_id, config_id=payload["feature_config_id"],
        )
        if not feature_config:
            raise HTTPException(status_code=422, detail=(
                f"Feature config {payload['feature_config_id']} does not exist in your organization."
            ))
    base_sensors = [str(n) for n in (feature_config or {}).get("base_sensors") or []]

    if feature_config and payload.get("equipment_id") and not payload.get("sensor_ids"):
        found = await repository.sensor_ids_by_name(company_id, payload["equipment_id"], base_sensors)
        missing = sorted(set(base_sensors) - set(found))
        if missing:
            raise HTTPException(status_code=422, detail=(
                f"Equipment {payload['equipment_id']} has no sensor named {', '.join(missing)}, so the "
                f"model's inputs cannot be read from it."
            ))
        payload["sensor_ids"] = [found[n] for n in base_sensors]

    profile = payload.get("reference_profile")
    if isinstance(profile, dict) and profile.get("refused"):
        # Already judged unreadable on its way here (a promotion's sample artifact, ADR-0040 dec 4).
        payload["reference_profile"] = drift.refused_baseline(str(profile["refused"]))
    elif profile:
        reason = (drift.supplied_scores_refusal(profile) if isinstance(profile, dict) else None) \
            or drift.sample_refusal(profile, base_sensors)
        payload["reference_profile"] = drift.refused_baseline(reason) if reason else drift.accepted_sample(profile)
    return drift.baseline_state(payload.get("reference_profile"))


async def register_model(payload: Dict[str, Any], company_id: str, repository) -> Dict[str, Any]:
    """The registration gate (ADR-0027 dec 5), for every path that creates a platform model: the
    registration request and the promotion of a notebook-registered version (ADR-0040 dec 2)."""

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

    await _admit_address_or_refuse(
        company_id, payload.get("mlflow_run_id"), payload.get("artifact_uri"), on_refused=422,
    )

    await _refuse_author_code_or_reject(
        payload.get("mlflow_run_id"), payload.get("artifact_uri"), on_refused=422,
    )

    compatibility = await _evaluate_or_reject(
        payload.get("mlflow_run_id"),
        on_incompatible=422,
        model_label=payload["model_name"],
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
            logger.warning(f"Refusing uploaded model '{payload['model_name']}': {verdict.reason}")
            raise HTTPException(status_code=422, detail={
                "message": verdict.reason,
                "declared_semantics": payload.get("score_semantics"),
                "flavor": compatibility.flavor if compatibility else None,
            })
        detector = verdict.detector
        if payload.get("detector"):
            # A caller may pick among the detectors that implement the declaration, never outside
            # them: a detector with other semantics would read this model's output as something else.
            matches = extensions.detectors_for(payload["score_semantics"], compatibility.flavor)
            if payload["detector"] not in matches:
                raise HTTPException(status_code=422, detail={
                    "message": (
                        f"The '{payload['detector']}' detector does not implement "
                        f"'{payload['score_semantics']}' for `{compatibility.flavor}` models."
                    ),
                    "detectors": matches,
                })
            detector = payload["detector"]
        payload["detector"] = detector
        logger.info(
            f"Uploaded model '{payload['model_name']}' declares '{payload.get('score_semantics')}'; "
            f"scored here by '{detector}'"
        )
    else:
        payload["detector"] = _kernel_detector_or_refuse(
            payload.get("detector"), compatibility.flavor if compatibility else None,
        )

    # Judged, not stored: the detector recorded above IS the declaration, and remains the authority
    # on what an output means (ADR-0028 dec 1). A second copy could only drift from it.
    payload.pop("score_semantics", None)

    baseline = await _bind_inputs_and_baseline(payload, company_id, repository)

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
                f"A model named '{payload['model_name']}' version {payload['model_version']} is "
                f"already registered. Register a new version rather than replacing one: a model "
                f"version is a record of what was served, and overwriting it would erase that."
            ),
        )

    if not model_id:
        raise HTTPException(status_code=500, detail="Failed to create model")

    logger.info(f"Model registered: {model_id} - {payload['model_name']}")

    return {
        "status": "success",
        "model_id": str(model_id),
        "model_name": payload["model_name"],
        "baseline": baseline,
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

        data['baseline'] = drift.baseline_state(data.pop('baseline_summary', None))
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
    
    model_data.pop('baseline_summary', None)
    model_data['baseline'] = drift.baseline_state(model_data.get('reference_profile'))
    return model_data


@router.post("/{model_id}/deploy", dependencies=[Depends(auth.require_writer)])
async def deploy_model(
    model_id: str,
    request_data: ModelDeployRequest,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """Update model status (deploy/undeploy)"""
    # The values the `ml_models.status` CHECK actually permits (03-tenant-ml-tables.sql). The list had
    # drifted to `staging`/`archived` — MLflow's stage names, which this table never adopted — so
    # deploying to either wrote a value the DB rejects and 500'd. `deprecated` is this schema's retire
    # state; `training`/`failed` are lifecycle outcomes, not operator-set deploy targets.
    valid_statuses = ['production', 'active', 'deprecated']
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
    # Undeploying is never blocked: deprecating a model is the remedy for a bad one, not another way
    # to break it. Only the states that put a model into a serving path run the load-and-score gate.
    if request_data.environment in ("production", "active"):
        model_label = str(model_data.get("model_name", model_id))

        # The address first, as at registration: a row registered before that check existed carries
        # whatever it was given, and this is the gate that puts it in front of the serving path.
        await _admit_address_or_refuse(
            company_id, model_data.get("mlflow_run_id"), model_data.get("artifact_uri"),
            on_refused=409,
        )

        # And the detector it names must still be loaded here (an extension can be removed between
        # registration and deployment). Without it the model would deploy and then fail every score.
        detector = model_data.get("detector")
        if not detector or extensions.get_detector(detector) is None:
            raise HTTPException(status_code=409, detail={
                "message": (
                    "This model names no detector this deployment has loaded, so nothing here can "
                    "say what its output means and it will not be deployed (ADR-0028)."
                    + ("" if detector else " Register it again to record one.")
                ),
                "detector": detector,
            })

        await _refuse_author_code_or_reject(
            model_data.get("mlflow_run_id"), model_data.get("artifact_uri"), on_refused=409,
        )
        uploaded = uploaded_model.provenance_of(
            mlflow_run_id=model_data.get("mlflow_run_id"),
            artifact_uri=model_data.get("artifact_uri"),
        ) == uploaded_model.PROVENANCE_UPLOADED

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
        # ADR-0035 dec 2, 3: the baseline's prediction scores are derived here, at every deployment, by
        # the gate's own load: the probe's for an upload, serving's for any other model.
        sample_plan = await baseline_gate.plan(request.app.state, repository, company_id, model_data)
        derived_profile = None

        if uploaded:
            result = await load_probe.probe(model_data["artifact_uri"],
                                            sample_plan.for_probe() if sample_plan else None)
            if sample_plan:
                derived_profile = sample_plan.finish(result.scores, result.sample_error)
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
        elif sample_plan:
            try:
                loaded = await load_model_or_refuse(company_id, model_data)
            except HTTPException as exc:
                if exc.status_code == 503:
                    raise
                # It cannot be loaded here, so it cannot serve here.
                raise HTTPException(status_code=409, detail=exc.detail) from exc
            _, detector_fn = detector_for(model_data)
            derived_profile = await baseline_gate.score_in_process(sample_plan, loaded, detector_fn)

        if derived_profile is not None:
            await repository.update_model_reference_profile(
                company_id=company_id, model_id=model_id, profile=derived_profile,
            )
            logger.info(f"Model '{model_label}' baseline: {drift.baseline_state(derived_profile)}")

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


@router.delete("/{model_id}", dependencies=[Depends(auth.require_writer)])
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
    
    # Deprecate (soft-delete) the model — to the DB's own retire state, not MLflow's `archived`.
    success = await repository.delete_model(
        company_id=company_id,
        model_id=model_id
    )

    if not success:
        raise HTTPException(status_code=500, detail="Failed to deprecate model")

    logger.info(f"Model {model_id} deprecated")

    return {
        "status": "deprecated",
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
