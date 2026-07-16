# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tenant-scoping policy for the experiment-tracking gateway (ADR-0019).

The gateway authenticates a notebook's tracking capability, resolves it to one tenant, and forces
every MLflow operation into that tenant's namespace. This module is the **pure** policy — the
decisions, no I/O:

  * resolve a tracking handle to its tenant (the audience check + the bound tenant);
  * the tenant namespace for experiment and registered-model **names** (a ``tenant_<uuid>/``
    prefix added on the way in, stripped on the way out), and the predicate for "does this
    qualified name belong to the caller's tenant";
  * the tenant prefix for **artifact** object-store keys;
  * a declarative map of which MLflow REST fields are tenant-namespaced names, so the gateway can
    rewrite requests and responses without spreading the tenant rule across call sites.

The live MLflow proxying, the experiment/run **id**-ownership lookups (which need a call to
MLflow), and the artifact pre-signed-URL minting live in ``gateway.py``; this layer is fully
unit-testable on dicts and strings.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

AUDIENCE_TRACKING = "tracking"  # the gateway's copy of the audience (ADR-0019 / ADR-0015 dec 3)
# The upload plane (ADR-0030 dec 3). It reaches this same mediator, so the audience is the ONLY fact
# separating the two populations — and they are held to different rules: an uploaded artifact faces
# the structural refusal in `admission`, a kernel logging a model does not (ADR-0030 dec 4 resolves
# ADR-0027's deferral for this path only, deliberately leaving the notebook path alone). Collapse the
# two audiences and there is no third outcome: either kernels inherit a refusal nothing decided for
# them, or uploads escape the refusal that is the precondition for admitting them at all.
AUDIENCE_UPLOAD = "upload"
_KEY_PREFIX = "nbcap:"


@dataclass(frozen=True)
class TrackingBinding:
    """The tenant a handle is bound to (ADR-0019). One tenant, always — a handle that writes is
    still bound to exactly one tenant (ADR-0015 dec 1 rev 1)."""

    user: str
    company_id: str
    audience: str = AUDIENCE_TRACKING


async def _resolve_binding(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str, expected_audience: str
) -> Optional[TrackingBinding]:
    """Resolve a handle of exactly one audience to its tenant, or None to deny.

    None means the gateway refuses. Absent/expired/revoked/malformed all deny (ADR-0015 dec 1-2),
    and the audience must match exactly — possession of one plane's handle never yields another's
    (dec 3). The audience is compared, never defaulted: a record with no audience denies.
    """
    if not handle:
        return None
    raw = await aget(f"{_KEY_PREFIX}{handle}")
    if raw is None:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode()
    try:
        record = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(record, dict):
        return None
    if record.get("audience") != expected_audience:
        return None
    company_id = record.get("company_id")
    if not company_id:
        return None
    return TrackingBinding(
        user=record.get("user", ""), company_id=company_id, audience=expected_audience
    )


async def resolve_tracking_binding(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str
) -> Optional[TrackingBinding]:
    """Resolve a tracking-audience handle to its tenant, or None to deny (ADR-0019 dec 4).

    An upload handle never resolves here: the tracking plane is the kernel's, and an uploader must
    not reach it by holding the wrong ticket.
    """
    return await _resolve_binding(aget, handle, AUDIENCE_TRACKING)


async def resolve_upload_binding(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str
) -> Optional[TrackingBinding]:
    """Resolve an upload-audience handle to its tenant, or None to deny (ADR-0030 dec 3).

    A tracking handle never resolves here — which is the point: see AUDIENCE_UPLOAD above.
    """
    return await _resolve_binding(aget, handle, AUDIENCE_UPLOAD)


def _tenant_token(company_id: str) -> str:
    """The per-tenant base token ``tenant_<uuid>`` (UUID-validated, ADR-0003)."""
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


def tenant_prefix(company_id: str) -> str:
    """The per-tenant prefix for experiment + registered-model **names**. Ends in ``.`` rather
    than ``/`` because MLflow forbids ``/`` and ``:`` in registered-model names (experiments allow
    both, but one prefix must serve both name spaces)."""
    return _tenant_token(company_id) + "."


# --------------------------------------------------------------------------- names

def qualify_name(company_id: str, name: str) -> str:
    """Map a kernel-supplied experiment/model name into the tenant namespace.

    Idempotent: a name already in the tenant's namespace is returned unchanged, so re-qualifying a
    value the gateway emitted is safe.
    """
    prefix = tenant_prefix(company_id)
    return name if name.startswith(prefix) else f"{prefix}{name}"


def owns_name(company_id: str, qualified: str) -> bool:
    """Whether a qualified name belongs to the caller's tenant."""
    return bool(qualified) and qualified.startswith(tenant_prefix(company_id))


def unqualify_name(company_id: str, qualified: str) -> Optional[str]:
    """Strip the tenant prefix for a response, or None if the name is not the caller's tenant's
    (so the gateway drops, never leaks, another tenant's entry)."""
    prefix = tenant_prefix(company_id)
    if not qualified or not qualified.startswith(prefix):
        return None
    return qualified[len(prefix):]


# --------------------------------------------------------------------------- artifacts

def artifact_prefix(company_id: str) -> str:
    """The object-store key prefix for a tenant's artifacts (decision 5/6). Uses ``/`` — it is a
    storage path, not an MLflow name, so the ``/``-restriction does not apply."""
    return _tenant_token(company_id) + "/"


def owns_artifact_key(company_id: str, key: str) -> bool:
    """Whether an object-store key is under the caller's tenant prefix — checked before the
    gateway signs a pre-signed URL for it (ADR-0019 dec 6)."""
    key = (key or "").lstrip("/")
    return key.startswith(artifact_prefix(company_id))


def scope_artifact_key(company_id: str, key: str) -> str:
    """Place a kernel-relative artifact path under the tenant prefix (idempotent)."""
    key = (key or "").lstrip("/")
    prefix = artifact_prefix(company_id)
    return key if key.startswith(prefix) else f"{prefix}{key}"


# --------------------------------------------------------------------------- MLflow REST map

# Which JSON fields of an MLflow REST endpoint are tenant-namespaced *names*. The gateway qualifies
# these on the request and unqualifies them on the response, so the tenant rule lives in one table
# rather than scattered across handlers. Endpoints not listed carry only ids (validated by an
# ownership lookup in the gateway) or no tenant-bearing field.
#   request:  fields in the request body/query to qualify (kernel name -> tenant_<uuid>/name)
#   response: dotted paths in the response to unqualify (drop entries not in the tenant)
NAME_FIELDS = {
    "experiments/create": {"request": ["name"], "response": []},
    "experiments/get-by-name": {"request": ["experiment_name"], "response": ["experiment.name"]},
    "experiments/get": {"request": [], "response": ["experiment.name"]},
    "experiments/update": {"request": ["new_name"], "response": []},
    "experiments/search": {"request": [], "response": ["experiments[].name"]},
    "registered-models/create": {"request": ["name"], "response": ["registered_model.name"]},
    "registered-models/get": {"request": ["name"], "response": ["registered_model.name"]},
    "registered-models/search": {"request": [], "response": ["registered_models[].name"]},
    "registered-models/update": {"request": ["name"], "response": ["registered_model.name"]},
    "registered-models/delete": {"request": ["name"], "response": []},
    "model-versions/create": {"request": ["name"], "response": ["model_version.name"]},
    "model-versions/get": {"request": ["name"], "response": ["model_version.name"]},
    "model-versions/search": {"request": [], "response": ["model_versions[].name"]},
}


def request_name_fields(endpoint: str) -> list[str]:
    """The request fields to qualify for an endpoint (empty if none)."""
    return list(NAME_FIELDS.get(endpoint, {}).get("request", []))


def response_name_paths(endpoint: str) -> list[str]:
    """The response paths to unqualify for an endpoint (empty if none)."""
    return list(NAME_FIELDS.get(endpoint, {}).get("response", []))
