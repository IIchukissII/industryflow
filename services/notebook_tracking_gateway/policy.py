# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Tenant-scoping policy for the experiment-tracking gateway (ADR-0019, ADR-0030).

The gateway authenticates a caller's capability, resolves it to one tenant, and forces every
operation into that tenant's namespace. Two populations reach it: a notebook kernel on the tracking
plane (ADR-0019), and a person uploading an externally-authored artifact (ADR-0030). They are held
to different admission rules, and the **audience** is the only fact that tells them apart — so
resolution is per-audience and never defaults.

This module is the **pure** policy — the decisions, no I/O:

  * resolve a handle of exactly one audience to its tenant (the audience check + the bound tenant);
  * the tenant namespace for experiment and registered-model **names** (a ``tenant_<uuid>.``
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
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

from industryflow_contracts import tenancy

import staging

AUDIENCE_TRACKING = "tracking"  # the gateway's copy of the audience (ADR-0019 / ADR-0015 dec 3)
# The upload plane (ADR-0030 dec 3). It reaches this same mediator, so the audience is the ONLY fact
# separating the two populations — and they are held to different rules: an uploaded artifact faces
# the structural refusal in `admission`, a kernel logging a model does not (ADR-0030 dec 4 resolves
# ADR-0027's deferral for this path only, deliberately leaving the notebook path alone). Collapse the
# two audiences and there is no third outcome: either kernels inherit a refusal nothing decided for
# them, or uploads escape the refusal that is the precondition for admitting them at all.
AUDIENCE_UPLOAD = "upload"
_KEY_PREFIX = "nbcap:"
# Present while the handle's company is suspended (ADR-0033 dec 3): its handles do not resolve.
# Written and removed by the platform API with the company's active flag; shared contract
# (services/api_gateway/capability_auth.py).
_SUSPENDED_PREFIX = "nbcap-suspended:"


@dataclass(frozen=True)
class TrackingBinding:
    """The tenant a handle is bound to (ADR-0019). One tenant, always — a handle that writes is
    still bound to exactly one tenant (ADR-0015 dec 1)."""

    user: str
    company_id: str
    audience: str = AUDIENCE_TRACKING


async def _resolve_binding(
    aget: Callable[[str], Awaitable[Optional[object]]], handle: str, expected_audience: str
) -> Optional[TrackingBinding]:
    """Resolve a handle of exactly one audience to its tenant, or None to deny.

    None means the gateway refuses. Absent/expired/revoked/malformed all deny (ADR-0015 dec 1-2),
    and the audience must match exactly — possession of one plane's handle never yields another's
    (dec 3). The audience is compared, never defaulted: a record with no audience denies. A handle
    whose tenant is suspended denies.
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
    try:
        # Canonical, or deny: the tenant becomes every namespace below, and a malformed one must be
        # a refusal rather than a 500 from the first place it is used.
        company_id = tenancy.canonical_company_id(record.get("company_id") or "")
    except ValueError:
        return None
    if await aget(f"{_SUSPENDED_PREFIX}{company_id}") is not None:
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


# The tenant's names come from the one tenant-naming module the serving side also uses (ADR-0003
# dec 5, 7), so the gateway and serving cannot spell a tenant differently. UUID validation there is
# what makes them safe to interpolate: the value becomes a namespace, and a namespace that can be
# anything is not a boundary.
#
# ``tenant_token`` — the bare ``tenant_<uuid>`` token. Public because the token, not one of the
# prefixes built from it, is what partitions the upload staging area, which sits outside every
# tenant prefix by design (ADR-0030).
tenant_token = tenancy.tenant_token
# ``tenant_prefix`` — the prefix of experiment + registered-model **names**, ending in ``.`` rather
# than ``/`` because MLflow forbids ``/`` and ``:`` in registered-model names (experiments allow
# both, but one prefix must serve both name spaces).
tenant_prefix = tenancy.mlflow_name_prefix


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

# The object-store key prefix for a tenant's artifacts (decision 5/6). Uses ``/`` — it is a storage
# path, not an MLflow name, so the ``/``-restriction does not apply.
artifact_prefix = tenancy.mlflow_artifact_prefix


def is_plain_key(key: str) -> bool:
    """Whether a key means only what it spells: no empty, ``.`` or ``..`` segment and no backslash. A
    prefix test on any other key proves nothing — a reader that normalises it can land under another
    tenant's prefix."""
    return "\\" not in key and all(seg not in ("", ".", "..") for seg in key.split("/"))


def owns_artifact_key(company_id: str, key: str) -> bool:
    """Whether an artifact URI's key is under the caller's tenant prefix (a model version's source,
    which a trusted reader later resolves itself). The key is still URI-encoded, so a percent-escape
    is refused too: decoded, it could spell a segment the plain-key test never saw."""
    key = (key or "").lstrip("/")
    return "%" not in key and is_plain_key(key) and key.startswith(artifact_prefix(company_id))


def experiment_artifact_location(company_id: str, nonce: str) -> str:
    """The artifact location the gateway records for an experiment it creates (decision 5).

    MLflow derives every run's and logged model's artifact location from this value, and anything
    that reads an artifact through MLflow (the serving side, which bypasses the gateway) resolves it
    from that record. So the record must name the tenant-prefixed path the bytes are actually stored
    under, or the record and the store disagree and a trusted reader finds nothing. It is always set
    by the gateway, never taken from the request: a kernel-chosen location could name another tenant's
    prefix and have a trusted reader fetch it. ``nonce`` keeps experiments apart inside the prefix,
    because the experiment id does not exist until MLflow assigns it.
    """
    return f"mlflow-artifacts:/{artifact_prefix(company_id)}{nonce}"


def is_upload_reserved(company_id: str, scoped_key: str) -> bool:
    """Whether a tenant-scoped artifact key lies where only the upload plane may write (ADR-0030).

    An admitted upload (``<tenant>/uploads/<id>/…``) was judged on its bytes before it was admitted;
    a kernel overwriting or deleting it through the artifact plane afterwards would put bytes there
    that nothing judged, and the serving side resolves uploads by exactly that path. The staging
    root's name is reserved too, inside a tenant's prefix, so no kernel path can pass for it.
    """
    key = (scoped_key or "").lstrip("/")
    prefix = artifact_prefix(company_id)
    relative = key[len(prefix):] if key.startswith(prefix) else key
    first = relative.split("/", 1)[0]
    return first in (staging.ADMITTED_ROOT.rstrip("/"), staging.STAGING_ROOT.rstrip("/"))


def store_key(handle: str) -> str:
    """The shared store key of a capability handle (the contract every verifier shares)."""
    return f"{_KEY_PREFIX}{handle}"


def scope_artifact_key(company_id: str, key: str) -> str:
    """Place a kernel-relative artifact path under the tenant prefix (idempotent).

    Raises ValueError for a key that is not plain (``is_plain_key``): prefixing it would not keep it
    inside the tenant.
    """
    key = (key or "").lstrip("/")
    if key and not is_plain_key(key.rstrip("/")):
        raise ValueError("artifact path is not a plain key")
    prefix = artifact_prefix(company_id)
    return key if key.startswith(prefix) else f"{prefix}{key}"


# --------------------------------------------------------------------------- MLflow REST map

# Which JSON fields of an MLflow REST endpoint are tenant-namespaced *names*. The gateway qualifies
# these on the request and unqualifies them on the response, so the tenant rule lives in one table
# rather than scattered across handlers. Which endpoints are forwarded at all, and the id-ownership
# rule each is held to, is ``routes.ROUTES`` (default deny); this table only says which fields are names.
#   request:  fields in the request body/query to qualify (kernel name -> tenant_<uuid>.name)
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
    "registered-models/rename": {"request": ["name", "new_name"], "response": ["registered_model.name"]},
    "registered-models/set-tag": {"request": ["name"], "response": []},
    "registered-models/delete-tag": {"request": ["name"], "response": []},
    "registered-models/get-latest-versions": {"request": ["name"], "response": ["model_versions[].name"]},
    "registered-models/alias": {"request": ["name"], "response": ["model_version.name"]},
    "model-versions/update": {"request": ["name"], "response": ["model_version.name"]},
    "model-versions/delete": {"request": ["name"], "response": []},
    "model-versions/set-tag": {"request": ["name"], "response": []},
    "model-versions/delete-tag": {"request": ["name"], "response": []},
    "model-versions/transition-stage": {"request": ["name"], "response": ["model_version.name"]},
    "model-versions/get-download-uri": {"request": ["name"], "response": []},
}


def request_name_fields(endpoint: str) -> list[str]:
    """The request fields to qualify for an endpoint (empty if none)."""
    return list(NAME_FIELDS.get(endpoint, {}).get("request", []))


def response_name_paths(endpoint: str) -> list[str]:
    """The response paths to unqualify for an endpoint (empty if none)."""
    return list(NAME_FIELDS.get(endpoint, {}).get("response", []))
