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
import re

logger = logging.getLogger(__name__)

# An MLflow run id as the tracking store issues it. Checked before it is interpolated into a search
# filter: a quote in a caller-supplied id would otherwise change the filter it lands in.
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def is_run_id(value) -> bool:
    return isinstance(value, str) and bool(_RUN_ID.match(value))


def resolve_model_uri(mlflow_run_id: str) -> str:
    """Return the URI that actually addresses this run's model.

    `models:/<model_id>` when the tracking store knows a logged model for the run (MLflow 3's layout),
    otherwise the legacy `runs:/<run_id>/model`.

    The store is asked for exactly the models this run produced (`source_run_id = '<run>'`), not for
    every model in the experiment and then filtered here: that read returns one PAGE, so in an
    experiment holding more logged models than a page carries, a run whose model sat on a later page
    resolved to the legacy URI — which addresses nothing on a real deployment. Every page is still
    followed, in case one run logged more models than a page holds.

    A failure to resolve falls back to the legacy URI, so a tracking store that is down degrades to
    the old behaviour rather than to no behaviour. The caller's own error handling — the ADR-0027
    gate, or the load path — reports what happens next. The one thing raised is ValueError for a
    value that is not a run id at all, because it would otherwise be spliced into the filter.
    """
    import mlflow  # imported lazily; see model_compat for why

    if not is_run_id(mlflow_run_id):
        raise ValueError(f"not an MLflow run id: {mlflow_run_id!r}")

    try:
        client = mlflow.MlflowClient()
        run = client.get_run(mlflow_run_id)
        page_token = None
        while True:
            page = client.search_logged_models(
                experiment_ids=[run.info.experiment_id],
                filter_string=f"source_run_id = '{mlflow_run_id}'",
                page_token=page_token,
            )
            for model in page:
                if getattr(model, "source_run_id", None) == mlflow_run_id:
                    uri = f"models:/{model.model_id}"
                    logger.debug(f"Run {mlflow_run_id} -> logged model {uri}")
                    return uri
            page_token = getattr(page, "token", None)
            if not page_token:
                break
    except Exception as exc:  # noqa: BLE001 — resolution is best-effort by design
        logger.warning(
            f"Could not resolve a logged model for run {mlflow_run_id} ({exc}); "
            f"falling back to the legacy runs:/ URI"
        )

    return f"runs:/{mlflow_run_id}/model"
