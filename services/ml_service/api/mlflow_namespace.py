# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pure tenant-namespace rules for the MLflow model registry (ADR-0019), with no I/O or framework
imports so they can be unit-tested in isolation — the ml_service counterpart of
``notebook_tracking_gateway/policy.py``.

The notebook tracking gateway stores every experiment and registered model as
``tenant_<uuid>.<name>`` (the dot is forced because MLflow forbids ``/`` and ``:`` in model names)
and strips the prefix for kernels. The frontend read-path reuses these rules so the browser sees the
same clean, tenant-isolated view.
"""
from typing import Any, Dict, List, Optional
import uuid


def tenant_token(company_id: str) -> str:
    """The UUID-validated ``tenant_<uuid>`` token. Matches ``repository.normalize_company_id_to_schema``
    and the gateway's ``policy._tenant_token`` (ADR-0003); validation makes it safe to interpolate
    into a SQL/MLflow-search fragment."""
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


def tenant_prefix(company_id: str) -> str:
    """The registered-model / experiment name prefix for a tenant: ``tenant_<uuid>.``."""
    return tenant_token(company_id) + "."


def strip_owned(prefix: str, qualified: str) -> Optional[str]:
    """Strip the tenant prefix off a qualified name, or ``None`` if it is not the tenant's (so the
    caller drops it rather than leaking another tenant's model)."""
    if not qualified or not qualified.startswith(prefix):
        return None
    return qualified[len(prefix):]


def pick_latest(versions: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The newest model version by numeric version, or ``None`` for an empty list."""
    numbered = [v for v in versions if str(v.get("version", "")).isdigit()]
    if not numbered:
        return None
    return max(numbered, key=lambda v: int(v["version"]))


def shape_summary(model: Dict[str, Any], prefix: str, metrics: Dict[str, float]) -> Optional[Dict[str, Any]]:
    """Shape one registry model into the clean, tenant-stripped summary the UI table renders, or
    ``None`` if the model is not the caller's tenant's."""
    name = strip_owned(prefix, model.get("name", ""))
    if name is None:
        return None
    latest = pick_latest(model.get("versions", []))
    return {
        "name": name,
        "description": model.get("description") or "",
        "creation_timestamp": model.get("creation_timestamp"),
        "last_updated_timestamp": model.get("last_updated_timestamp"),
        "latest_version": latest.get("version") if latest else None,
        "stage": latest.get("current_stage") if latest else None,
        "run_id": latest.get("run_id") if latest else None,
        "metrics": metrics,
        "source": "notebook",
    }
