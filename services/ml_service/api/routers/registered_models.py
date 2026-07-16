# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Registered-models read-path — the SESSION-authed, tenant-safe view of the MLflow model registry
for the frontend (ADR-0019).

The notebook tracking gateway namespaces every experiment and registered model the data scientists
create as ``tenant_<uuid>.<name>`` and strips that prefix for their kernels. This router applies the
SAME convention for the browser: it lists only the caller tenant's models (a server-side
``name LIKE 'tenant_<uuid>.%'`` filter, re-verified here so a bad filter can never leak another
tenant) and strips the prefix so the UI shows clean names. It is read-only apart from editing a
model's free-text ``description``; model/version creation stays in the kernel→gateway path.

This REPLACES the removed ``/api/mlflow/*`` endpoints, which set a tenant ``search_path`` on
MLflow's *shared* database — a no-op that leaked every tenant's experiments and models.

The MLflow REST round-trips (``MlflowClient``) are the cluster-bound part; the pure namespace
helpers (``tenant_prefix`` / ``strip_owned`` / ``pick_latest`` / ``shape_summary``) carry the
tenant rule and are unit-tested without a live MLflow.
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from mlflow.tracking import MlflowClient
from mlflow.exceptions import MlflowException

from mlflow_namespace import is_safe_name, pick_latest, shape_summary, tenant_prefix
from routers.models import get_company_id_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/registered-models", tags=["Registered Models"])

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")


def _reject_unsafe_name(name: str) -> None:
    if not is_safe_name(name):
        raise HTTPException(status_code=400, detail="invalid model name")


# --------------------------------------------------------------------------- MLflow I/O (cluster-bound)

def _client() -> MlflowClient:
    return MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)


def _version_to_dict(v) -> Dict[str, Any]:
    return {
        "version": v.version,
        "current_stage": v.current_stage,
        "run_id": v.run_id,
        "status": v.status,
        "creation_timestamp": v.creation_timestamp,
    }


def _run_metrics(client: MlflowClient, run_id: Optional[str]) -> Dict[str, float]:
    if not run_id:
        return {}
    try:
        return dict(client.get_run(run_id).data.metrics)
    except MlflowException:
        return {}  # source run deleted/unreachable — show the model without metrics


class DescriptionUpdate(BaseModel):
    description: str


@router.get("")
async def list_registered_models(
    request: Request,
    company_id: str = Depends(get_company_id_dependency),
):
    """List the caller tenant's MLflow registered models with their latest version + source-run
    metrics, names stripped to the plain form the data scientist used."""
    prefix = tenant_prefix(company_id)

    def _work() -> List[Dict[str, Any]]:
        client = _client()
        models = client.search_registered_models(
            filter_string=f"name LIKE '{prefix}%'", max_results=1000
        )
        out: List[Dict[str, Any]] = []
        for m in models:
            latest = pick_latest([_version_to_dict(v) for v in (m.latest_versions or [])])
            metrics = _run_metrics(client, latest.get("run_id") if latest else None)
            shaped = shape_summary(
                {
                    "name": m.name,
                    "description": m.description,
                    "creation_timestamp": m.creation_timestamp,
                    "last_updated_timestamp": m.last_updated_timestamp,
                    "versions": [_version_to_dict(v) for v in (m.latest_versions or [])],
                },
                prefix,
                metrics,
            )
            if shaped:  # defensive: skip anything the LIKE filter returned outside the prefix
                out.append(shaped)
        return out

    try:
        models = await asyncio.to_thread(_work)
    except MlflowException as e:
        logger.warning("registered-models list failed: %s", e)
        raise HTTPException(status_code=502, detail="MLflow registry unavailable")
    return {"total": len(models), "models": models}


@router.get("/{name}")
async def get_registered_model(
    name: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency),
):
    """A model's full version history with each version's source-run metrics + params."""
    _reject_unsafe_name(name)
    prefix = tenant_prefix(company_id)
    qualified = prefix + name

    def _work() -> Optional[Dict[str, Any]]:
        client = _client()
        try:
            model = client.get_registered_model(qualified)
        except MlflowException:
            return None
        versions = client.search_model_versions(f"name = '{qualified}'")
        vlist: List[Dict[str, Any]] = []
        for v in sorted(versions, key=lambda x: int(x.version), reverse=True):
            run = None
            if v.run_id:
                try:
                    run = client.get_run(v.run_id)
                except MlflowException:
                    run = None
            vlist.append({
                **_version_to_dict(v),
                "metrics": dict(run.data.metrics) if run else {},
                "params": dict(run.data.params) if run else {},
            })
        return {
            "name": name,
            "description": model.description or "",
            "creation_timestamp": model.creation_timestamp,
            "last_updated_timestamp": model.last_updated_timestamp,
            "versions": vlist,
            "source": "notebook",
        }

    try:
        model = await asyncio.to_thread(_work)
    except MlflowException as e:
        logger.warning("registered-model get failed: %s", e)
        raise HTTPException(status_code=502, detail="MLflow registry unavailable")
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return model


@router.patch("/{name}")
async def update_registered_model_description(
    name: str,
    body: DescriptionUpdate,
    request: Request,
    company_id: str = Depends(get_company_id_dependency),
):
    """Edit a model's free-text description (the one write the UI allows). The name is re-qualified
    with the caller's prefix, so a tenant can only ever edit its own model."""
    _reject_unsafe_name(name)
    prefix = tenant_prefix(company_id)
    qualified = prefix + name

    def _work() -> None:
        client = _client()
        client.get_registered_model(qualified)  # 404s via MlflowException if not the tenant's
        client.update_registered_model(qualified, description=body.description)

    try:
        await asyncio.to_thread(_work)
    except MlflowException:
        raise HTTPException(status_code=404, detail="Model not found")
    return {"name": name, "description": body.description}
