# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Request/response tenant-scoping for MLflow REST calls (ADR-0019), built on the pure name rules in
``policy.py``. Still pure — it transforms JSON dicts, no I/O:

  * ``scope_request`` qualifies the kernel-supplied name fields of a request into the tenant
    namespace before the gateway proxies it to MLflow.
  * ``scope_response`` strips the tenant prefix from the name fields of a response, and **drops any
    entry that is not the caller's tenant's** — so a list endpoint can never leak another tenant's
    experiment or model even if MLflow returned it.

Id-bearing operations (a ``run_id`` or ``experiment_id`` with no name in the body) are not scoped
here; the gateway validates their ownership with a lookup before proxying (``gateway.py``).
"""
from __future__ import annotations

import copy
from typing import Any

import policy


def scope_request(endpoint: str, payload: dict, company_id: str) -> dict:
    """Return a copy of the request with its tenant-namespaced name fields qualified."""
    out = copy.deepcopy(payload) if payload else {}
    for field in policy.request_name_fields(endpoint):
        if field in out and isinstance(out[field], str):
            out[field] = policy.qualify_name(company_id, out[field])
    return out


def scope_response(endpoint: str, payload: dict, company_id: str) -> dict:
    """Return a copy of the response with tenant names stripped and foreign entries dropped."""
    out = copy.deepcopy(payload) if payload else {}
    for path in policy.response_name_paths(endpoint):
        _apply_name_path(out, path, company_id)
    return out


def _apply_name_path(obj: Any, path: str, company_id: str) -> None:
    """Walk a dotted path (supporting one ``[]`` list segment) and unqualify the leaf ``name``,
    dropping list items or nulling objects whose name is not the caller's tenant's."""
    head, _, tail = path.partition(".")
    if head.endswith("[]"):
        key = head[:-2]
        items = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(items, list):
            return
        kept = []
        for item in items:
            if _unqualify_leaf(item, tail, company_id):
                kept.append(item)
        obj[key] = kept
    elif tail:
        child = obj.get(head) if isinstance(obj, dict) else None
        if isinstance(child, dict):
            if not _unqualify_leaf(child, tail, company_id):
                obj[head] = None
    else:
        _unqualify_leaf(obj, head, company_id)


def _unqualify_leaf(obj: Any, field: str, company_id: str) -> bool:
    """Unqualify ``obj[field]`` in place. Returns True if it belongs to the tenant (keep), False
    if it is another tenant's (the caller should drop the containing entry)."""
    if not isinstance(obj, dict) or field not in obj or not isinstance(obj[field], str):
        return True  # nothing to scope on this entry
    plain = policy.unqualify_name(company_id, obj[field])
    if plain is None:
        return False
    obj[field] = plain
    return True
