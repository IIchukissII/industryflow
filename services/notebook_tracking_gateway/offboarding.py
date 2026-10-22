# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Empties a deleted tenant out of the tracking store, for the tenant-delete job (ADR-0033 dec 4).

The gateway is the artifact store's one writer (ADR-0030 dec 2), so the tenant's artifacts and staged
uploads are removed here, with its own principal. Experiments, runs and models are removed through
MLflow's API, never by touching its database: registered models are deleted outright (MLflow keeps no
trash for them); each experiment is renamed out of the tenant's name space, to
``tenancy.mlflow_deleted_name_prefix``, and soft-deleted with its runs and logged models. The tracking
server's own collection then hard-deletes what sits there — only there, so a tenant's own trash is
never collected on a deletion's behalf. Artifacts go first: the collection removes a run's artifacts
with the server's read-only principal, which can only succeed once there is nothing left to remove.

Every step is idempotent, so a call that dies part way is repeated whole. The result is what still
remains; the tenant is out of the tracking store when all of it is zero.
"""
from __future__ import annotations

from typing import Any, Optional

from industryflow_contracts import tenancy

import policy
import staging

_EXPERIMENT_PAGE = 1000
_MODEL_PAGE = 1000
_LOGGED_MODEL_PAGE = 100


class OffboardingError(RuntimeError):
    """MLflow refused a step; the caller retries the whole operation."""


async def empty_tenant(upstream, artifacts, company_id: str) -> dict:
    """Remove the tenant from the tracking store as far as the gateway can, and say what remains."""
    names = tenancy.mlflow_name_prefix(company_id)
    deleted = tenancy.mlflow_deleted_name_prefix(company_id)

    for name in await _registered_models(upstream, names):
        await _call(upstream, "DELETE", "registered-models/delete", {"name": name}, absent_ok=True)

    for key in _artifact_keys(artifacts, company_id):
        artifacts.delete(key)

    for experiment in await _experiments(upstream, names):
        eid = experiment["experiment_id"]
        if experiment.get("lifecycle_stage") == "deleted":
            # A deleted experiment cannot be renamed; restored, it is renamed and deleted below.
            await _call(upstream, "POST", "experiments/restore", {"experiment_id": eid})
        # Keyed by its id, so two experiments never collide on the new name.
        await _call(upstream, "POST", "experiments/update", {"experiment_id": eid, "new_name": f"{deleted}{eid}"})
    # Every experiment is under the deleted prefix now, including one a crashed call renamed and
    # never deleted.
    for experiment in await _experiments(upstream, deleted):
        if experiment.get("lifecycle_stage") == "deleted":
            continue
        eid = experiment["experiment_id"]
        for model_id in await _logged_models(upstream, eid):
            await _call(upstream, "DELETE", f"logged-models/{model_id}", None, absent_ok=True)
        await _call(upstream, "POST", "experiments/delete", {"experiment_id": eid})

    return await remaining(upstream, artifacts, company_id)


async def remaining(upstream, artifacts, company_id: str) -> dict:
    """What of the tenant the tracking store still holds. Deleted experiments count until collected."""
    names = tenancy.mlflow_name_prefix(company_id)
    deleted = tenancy.mlflow_deleted_name_prefix(company_id)
    return {
        "experiments": len(await _experiments(upstream, names)) + len(await _experiments(upstream, deleted)),
        "registered_models": len(await _registered_models(upstream, names)),
        "artifacts": len(_artifact_keys(artifacts, company_id)),
    }


def _artifact_keys(artifacts, company_id: str) -> list:
    token = policy.tenant_token(company_id)
    return (artifacts.list_keys(policy.artifact_prefix(company_id))
            + artifacts.list_keys(f"{staging.STAGING_ROOT}{token}/"))


async def _experiments(upstream, prefix: str) -> list:
    """Every experiment, active or deleted, whose name starts with ``prefix``."""
    found, token = [], None
    while True:
        # LIKE reads `_` as a wildcard, so the filter selects candidates; the prefix test decides.
        body = {"filter": f"name LIKE '{prefix}%'", "view_type": "ALL", "max_results": _EXPERIMENT_PAGE}
        if token:
            body["page_token"] = token
        data = await _call(upstream, "POST", "experiments/search", body)
        found += [e for e in data.get("experiments", []) if e.get("name", "").startswith(prefix)]
        token = data.get("next_page_token")
        if not token:
            return found


async def _registered_models(upstream, prefix: str) -> list:
    found, token = [], None
    while True:
        params = {"filter": f"name LIKE '{prefix}%'", "max_results": _MODEL_PAGE}
        if token:
            params["page_token"] = token
        data = await _call(upstream, "GET", "registered-models/search", params)
        found += [m["name"] for m in data.get("registered_models", []) if m.get("name", "").startswith(prefix)]
        token = data.get("next_page_token")
        if not token:
            return found


async def _logged_models(upstream, experiment_id: str) -> list:
    found, token = [], None
    while True:
        body = {"experiment_ids": [experiment_id], "max_results": _LOGGED_MODEL_PAGE}
        if token:
            body["page_token"] = token
        data = await _call(upstream, "POST", "logged-models/search", body)
        found += [m["info"]["model_id"] for m in data.get("models", [])]
        token = data.get("next_page_token")
        if not token:
            return found


async def _call(upstream, method: str, endpoint: str, message: Optional[dict], *, absent_ok: bool = False) -> Any:
    if method == "GET":
        status, data = await upstream.call(method, endpoint, params=message or {}, body=None)
    else:
        status, data = await upstream.call(method, endpoint, params={}, body=message)
    if status == 404 and absent_ok:
        return {}
    if status != 200:
        raise OffboardingError(f"{method} {endpoint}: MLflow answered {status} {data}")
    return data if isinstance(data, dict) else {}
