# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Experiment/run read-path — the SESSION-authed, tenant-safe view of MLflow experiments and their runs
for the frontend (ADR-0019).

ADR-0019 records experiment/run browsing as owed: the read-path it needs was removed with the
``/api/mlflow/*`` shim, which set a tenant ``search_path`` on MLflow's *shared* database — a no-op
that returned every tenant's experiments. Nothing replaced it, so the browser has had no way to see
experiments since. This router is that replacement, built on the same convention the tracking
gateway already enforces for kernels: every experiment is stored as ``tenant_<uuid>.<name>``, listed
behind a server-side prefix filter, and stripped to the plain name on the way out.

The tenant rule is applied twice on purpose. Listing filters by prefix in MLflow, and
``shape_experiment`` re-checks the prefix on every row it returns, so a filter that is wrong (or an
MLflow that ignores it) still cannot leak another tenant's experiment. Runs are reached only
*through* an experiment this tenant owns — never by a caller-supplied id — so run ownership is
decided once, at the experiment boundary, rather than re-derived per run.

The MLflow REST round-trips (``MlflowClient``) are the cluster-bound part; the pure namespace
helpers (``tenant_prefix`` / ``shape_experiment`` / ``shape_run``) carry the tenant rule and are
unit-tested without a live MLflow.
"""
import asyncio
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from mlflow.tracking import MlflowClient
from mlflow.exceptions import MlflowException

from mlflow_namespace import is_safe_name, shape_experiment, shape_run, tenant_prefix
from routers.models import get_company_id_dependency

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/experiments", tags=["Experiments"])

MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")

# A run list is a browse view, not an export; cap it so one long-running experiment cannot make the
# UI (or this service) pull an unbounded result set.
_MAX_RUNS = 500
_MAX_EXPERIMENTS = 1000


def _client() -> MlflowClient:
    return MlflowClient(tracking_uri=MLFLOW_TRACKING_URI)


def _experiment_to_dict(e) -> Dict[str, Any]:
    return {
        "name": e.name,
        "experiment_id": e.experiment_id,
        "lifecycle_stage": e.lifecycle_stage,
        "creation_time": getattr(e, "creation_time", None),
        "last_update_time": getattr(e, "last_update_time", None),
    }


def _run_to_dict(r) -> Dict[str, Any]:
    return {
        "run_id": r.info.run_id,
        "run_name": getattr(r.info, "run_name", "") or r.data.tags.get("mlflow.runName", ""),
        "status": r.info.status,
        "start_time": r.info.start_time,
        "end_time": r.info.end_time,
        "metrics": dict(r.data.metrics),
        "params": dict(r.data.params),
    }


@router.get("")
async def list_experiments(
    request: Request,
    company_id: str = Depends(get_company_id_dependency),
):
    """List the caller tenant's MLflow experiments, names stripped to the plain form the data
    scientist used."""
    prefix = tenant_prefix(company_id)

    def _work() -> List[Dict[str, Any]]:
        client = _client()
        experiments = client.search_experiments(
            filter_string=f"name LIKE '{prefix}%'", max_results=_MAX_EXPERIMENTS
        )
        out: List[Dict[str, Any]] = []
        for e in experiments:
            shaped = shape_experiment(_experiment_to_dict(e), prefix)
            if shaped:  # defensive: skip anything the LIKE filter returned outside the prefix
                out.append(shaped)
        return out

    try:
        experiments = await asyncio.to_thread(_work)
    except MlflowException as e:
        logger.warning("experiments list failed: %s", e)
        raise HTTPException(status_code=502, detail="MLflow tracking unavailable")
    return {"total": len(experiments), "experiments": experiments}


@router.get("/{name}/runs")
async def list_experiment_runs(
    name: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency),
    limit: int = Query(_MAX_RUNS, ge=1, le=_MAX_RUNS),
):
    """The runs of one of the caller tenant's experiments, newest first, with each run's metrics and
    params.

    The experiment is addressed by its plain name and re-qualified with the caller's prefix, so a
    tenant can only ever reach its own experiment — and therefore only its own runs.
    """
    if not is_safe_name(name):
        raise HTTPException(status_code=400, detail="invalid experiment name")
    prefix = tenant_prefix(company_id)
    qualified = prefix + name

    def _work() -> Optional[Tuple[Dict[str, Any], List[Dict[str, Any]]]]:
        client = _client()
        experiment = client.get_experiment_by_name(qualified)
        if experiment is None:
            return None
        shaped = shape_experiment(_experiment_to_dict(experiment), prefix)
        if shaped is None:  # unreachable via the re-qualified name; belt-and-braces
            return None
        runs = client.search_runs(
            experiment_ids=[experiment.experiment_id],
            max_results=limit,
            order_by=["attributes.start_time DESC"],
        )
        return shaped, [shape_run(_run_to_dict(r)) for r in runs]

    try:
        found = await asyncio.to_thread(_work)
    except MlflowException as e:
        logger.warning("experiment runs list failed: %s", e)
        raise HTTPException(status_code=502, detail="MLflow tracking unavailable")
    if found is None:
        raise HTTPException(status_code=404, detail="Experiment not found")
    experiment, runs = found
    return {"experiment": experiment, "total": len(runs), "runs": runs}
