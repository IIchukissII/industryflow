# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The MLflow REST routes the gateway forwards, and the tenant rule each one is held to (ADR-0019 dec 5).

DEFAULT DENY. A route that is not in ``ROUTES`` is refused, never proxied. The previous shape was the
opposite: every route was forwarded and only the ones on a short list were checked, so each MLflow
route nobody had thought about (logged models, metric history, experiment rename, registry tags) was a
silent cross-tenant path. A new MLflow route now fails closed until someone writes down its rule here.

Pure: no I/O. The gateway performs the lookups each rule names.

Rules (a route may carry several; all must hold):

  ``experiment``   every ``experiment_id`` / ``experiment_ids`` in the request is the caller's; at
                   least one must be present.
  ``run``          the run named by ``run_id`` (or its legacy alias ``run_uuid``) is the caller's;
                   one must be present.
  ``runs``         every id in ``run_ids`` is the caller's; at least one must be present.
  ``run_opt``      as ``run``, but only if the request names a run.
  ``logged_model`` the logged model named in the path (or ``model_id``) is the caller's.
  ``logged_model_opt`` as ``logged_model``, but only if the request names one.
  ``names``        the request's tenant-namespaced name fields are qualified (``policy.NAME_FIELDS``).
                   Qualifying is what scopes these routes: a name the caller sends always lands in its
                   own namespace, even one spelled with another tenant's prefix.
  ``mv_source``    a model version's ``source`` points into the caller's tenant (see ``source_kind``).
  ``create_experiment`` name qualified and artifact location forced by the gateway.
  ``filtered``     no request id to check; the response is filtered to the caller's tenant.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import policy


@dataclass(frozen=True)
class Route:
    endpoint: str          # the MLflow route template, e.g. "logged-models/{model_id}"
    rules: tuple[str, ...]


def _r(method: str, endpoint: str, *rules: str) -> tuple[tuple[str, str], Route]:
    return (method, endpoint), Route(endpoint, rules)


ROUTES: dict[tuple[str, str], Route] = dict([
    # experiments
    _r("POST", "experiments/create", "create_experiment"),
    _r("GET", "experiments/get", "experiment"),
    _r("GET", "experiments/get-by-name", "names"),
    _r("GET", "experiments/search", "filtered", "names"),
    _r("POST", "experiments/search", "filtered", "names"),
    _r("POST", "experiments/update", "experiment", "names"),
    _r("POST", "experiments/delete", "experiment"),
    _r("POST", "experiments/restore", "experiment"),
    _r("POST", "experiments/set-experiment-tag", "experiment"),
    _r("POST", "experiments/delete-experiment-tag", "experiment"),
    # runs
    _r("POST", "runs/create", "experiment"),
    _r("POST", "runs/search", "experiment"),
    _r("GET", "runs/get", "run"),
    _r("POST", "runs/update", "run"),
    _r("POST", "runs/delete", "run"),
    _r("POST", "runs/restore", "run"),
    _r("POST", "runs/log-metric", "run"),
    _r("POST", "runs/log-parameter", "run"),
    _r("POST", "runs/log-batch", "run"),
    _r("POST", "runs/log-inputs", "run"),
    _r("POST", "runs/outputs", "run"),
    _r("POST", "runs/log-model", "run"),
    _r("POST", "runs/set-tag", "run"),
    _r("POST", "runs/delete-tag", "run"),
    _r("GET", "metrics/get-history", "run"),
    _r("GET", "metrics/get-history-bulk-interval", "runs"),
    _r("GET", "artifacts/list", "run"),
    # logged models (MLflow 3)
    _r("POST", "logged-models", "experiment", "run_opt"),
    _r("POST", "logged-models/search", "experiment"),
    _r("GET", "logged-models/{model_id}", "logged_model"),
    _r("PATCH", "logged-models/{model_id}", "logged_model"),
    _r("DELETE", "logged-models/{model_id}", "logged_model"),
    _r("POST", "logged-models/{model_id}/params", "logged_model"),
    _r("PATCH", "logged-models/{model_id}/tags", "logged_model"),
    _r("DELETE", "logged-models/{model_id}/tags/{tag_key}", "logged_model"),
    _r("GET", "logged-models/{model_id}/artifacts/directories", "logged_model"),
    # registered models
    _r("POST", "registered-models/create", "names"),
    _r("GET", "registered-models/get", "names"),
    _r("GET", "registered-models/search", "filtered", "names"),
    _r("PATCH", "registered-models/update", "names"),
    _r("DELETE", "registered-models/delete", "names"),
    _r("POST", "registered-models/rename", "names"),
    _r("POST", "registered-models/set-tag", "names"),
    _r("DELETE", "registered-models/delete-tag", "names"),
    _r("GET", "registered-models/get-latest-versions", "names"),
    _r("POST", "registered-models/get-latest-versions", "names"),
    _r("POST", "registered-models/alias", "names"),
    _r("DELETE", "registered-models/alias", "names"),
    _r("GET", "registered-models/alias", "names"),
    # model versions
    _r("POST", "model-versions/create", "names", "mv_source", "run_opt", "logged_model_opt"),
    _r("GET", "model-versions/get", "names"),
    _r("GET", "model-versions/search", "filtered", "names"),
    _r("PATCH", "model-versions/update", "names"),
    _r("DELETE", "model-versions/delete", "names"),
    _r("POST", "model-versions/set-tag", "names"),
    _r("DELETE", "model-versions/delete-tag", "names"),
    _r("POST", "model-versions/transition-stage", "names"),
    _r("GET", "model-versions/get-download-uri", "names"),
])

# Template segments like {model_id} match exactly one path segment.
def _compile(template: str) -> re.Pattern:
    parts = ["[^/]+" if seg.startswith("{") else re.escape(seg) for seg in template.split("/")]
    return re.compile("^" + "/".join(parts) + "$")


_PATTERNS = [(method, _compile(ep), route) for (method, ep), route in ROUTES.items() if "{" in ep]


def match(method: str, endpoint: str) -> Optional[tuple[Route, list[str]]]:
    """The route rule for a request and its path parameters, or None (refuse) if it is not allowed."""
    endpoint = endpoint.strip("/")
    exact = ROUTES.get((method, endpoint))
    if exact is not None:
        return exact, []
    for m, pattern, route in _PATTERNS:
        if m == method:
            if pattern.match(endpoint):
                templ = route.endpoint.split("/")
                return route, [seg for t, seg in zip(templ, endpoint.split("/")) if t.startswith("{")]
    return None


# --------------------------------------------------------------------------- model-version sources

_RUNS_SOURCE = re.compile(r"^runs:/([0-9a-f]{32})(?:/.*)?$")
_LOGGED_MODEL_SOURCE = re.compile(r"^models:/(m-[0-9a-f]{32})/?$")
_ARTIFACT_SOURCE = "mlflow-artifacts:/"


def source_kind(source: str) -> tuple[str, str]:
    """Classify a model version ``source`` into what it points at.

    Returns ``("run", run_id)``, ``("logged_model", model_id)``, ``("artifact", key)`` or
    ``("refused", reason)``. Anything else — a raw object-store URI, a local path, a registered-model
    URI — is refused: a source the gateway cannot attribute to the caller's tenant could name another
    tenant's bytes, and the serving side would load them as this tenant's model.
    """
    source = (source or "").strip()
    if m := _RUNS_SOURCE.match(source):
        return "run", m.group(1)
    if m := _LOGGED_MODEL_SOURCE.match(source):
        return "logged_model", m.group(1)
    if source.startswith(_ARTIFACT_SOURCE):
        return "artifact", source[len(_ARTIFACT_SOURCE):]
    return "refused", "model version source must be runs:/<run>, models:/<logged model> or a tenant artifact path"


def _check_table() -> None:
    """A rule that names a table entry which does not exist would silently check nothing. Fail at import."""
    for (method, ep), route in ROUTES.items():
        if "names" in route.rules and "filtered" not in route.rules and not policy.request_name_fields(ep):
            raise RuntimeError(f"{method} {ep}: 'names' rule but no request name fields in policy.NAME_FIELDS")
        if "filtered" in route.rules and not policy.response_name_paths(ep):
            raise RuntimeError(f"{method} {ep}: 'filtered' rule but no response paths in policy.NAME_FIELDS")


_check_table()
