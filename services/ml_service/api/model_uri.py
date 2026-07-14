# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Where a model actually LIVES in MLflow 3 (#240).

The serving path used to load every model with `runs:/<run_id>/model`. On a real deployment that
resolves to nothing:

    MLflow 3 does not store a logged model under its run. It stores it in the logged-models area —
    `<experiment>/models/m-<model_id>/artifacts/…` — and NO object under the run id exists at all.

Against a local artifact directory the `runs:/` URI still happens to resolve, which is why CI and every
local test were green. Against the deployed topology (artifacts proxied to S3/MinIO) the tracking
server 404s on the run-scoped path and the client retries until it hangs. Found by box validation, and
it meant `ml_service` could not load a single notebook-trained model on a real stack.

The tracking store already holds the mapping (`source_run_id` -> `model_id`), so the fix is to ask it
rather than to assume a path. `runs:/` is kept as a fallback: artifacts logged by older clients really
do live under the run, and this must not break them.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def resolve_model_uri(mlflow_run_id: str) -> str:
    """Return the URI that actually addresses this run's model.

    `models:/<model_id>` when the tracking store knows a logged model for the run (MLflow 3's layout),
    otherwise the legacy `runs:/<run_id>/model`.

    Never raises: a failure to resolve falls back to the legacy URI, so a tracking store that is down
    degrades to the old behaviour rather than to no behaviour. The caller's own error handling — the
    ADR-0027 gate, or the load path — reports what happens next.
    """
    import mlflow  # imported lazily; see model_compat for why

    try:
        client = mlflow.MlflowClient()
        run = client.get_run(mlflow_run_id)
        logged = client.search_logged_models(experiment_ids=[run.info.experiment_id])
        for model in logged:
            if getattr(model, "source_run_id", None) == mlflow_run_id:
                uri = f"models:/{model.model_id}"
                logger.debug(f"Run {mlflow_run_id} -> logged model {uri}")
                return uri
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort by design
        logger.warning(
            f"Could not resolve a logged model for run {mlflow_run_id} ({exc}); "
            f"falling back to the legacy runs:/ URI"
        )

    return f"runs:/{mlflow_run_id}/model"


def resolve_model_uri_or(mlflow_run_id: Optional[str]) -> Optional[str]:
    """`resolve_model_uri`, tolerating a model that has no run at all."""
    return resolve_model_uri(mlflow_run_id) if mlflow_run_id else None
