# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The tracking store's own collection of deleted tenants (ADR-0033 dec 4).

The tracking gateway empties a deleted tenant through MLflow's API: it renames each of the tenant's
experiments under DELETED_NAME_ROOT and soft-deletes it with its runs and logged models, and it
removes the tenant's artifacts. This hard-deletes exactly those experiments, their runs and their
logged models with MLflow's own garbage collection, run from the tracking server's image with the
server's credentials, which the tenant-delete job never holds.

Scoped by construction: only a deleted experiment named under DELETED_NAME_ROOT is collected. A
kernel's names are always forced under its tenant's prefix, so no tenant can put one there, and a
tenant's own deleted entries, still restorable, are never touched. ``mlflow gc`` itself collects
every deleted run, experiment and logged model of every tenant when an id list is absent or empty,
so it is called here once per experiment with all three lists given; never the CLI, where an
empty ``--run-ids`` silently becomes "all".

A run's artifacts are removed through the tracking server (MLFLOW_TRACKING_URI), whose object-store
principal cannot delete: that succeeds only where the gateway already emptied the tenant's prefix,
and anything still there stops the collection of that experiment. A failed experiment is retried
on the next run.

Env: BACKEND_STORE_URI (the tracking server's backend store; its password from PGPASSWORD),
     MLFLOW_TRACKING_URI (the tracking server).
"""
from __future__ import annotations

import logging
import os
import sys

from mlflow.cli import _gc_tracking_resources
from mlflow.entities import LifecycleStage, ViewType
from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore

# tenancy.MLFLOW_DELETED_NAME_ROOT; the contracts package is not in this image, so a guard
# (tests/config_guards/test_tracking_collect.py) holds the two equal.
DELETED_NAME_ROOT = "_deleted."
_DELETED_TENANT = DELETED_NAME_ROOT + "tenant_"

logger = logging.getLogger("collect_deleted")


def _all(fetch) -> list:
    out, token = [], None
    while True:
        page = fetch(token)
        out += list(page)
        token = page.token
        if not token:
            return out


def collectable(store) -> list:
    """The deleted experiments of deleted tenants."""
    found = _all(lambda t: store.search_experiments(
        view_type=ViewType.DELETED_ONLY, filter_string=f"name LIKE '{DELETED_NAME_ROOT}%'", page_token=t))
    # LIKE reads `_` as a wildcard, so the filter selects candidates; the prefix test decides.
    return [e for e in found if e.name.startswith(_DELETED_TENANT)]


def deleted_logged_models(store) -> dict:
    """experiment id -> the ids of its deleted logged models (a deleted one no search returns)."""
    out: dict = {}
    for model_id in store._get_deleted_logged_models():
        model = store.get_logged_model(model_id, allow_deleted=True)
        out.setdefault(model.experiment_id, []).append(model_id)
    return out


def collect(store, experiment, logged_model_ids: list) -> int:
    """Hard-delete one deleted experiment with its runs and logged models; returns the runs removed."""
    eid = experiment.experiment_id
    runs = _all(lambda t: store.search_runs([eid], "", ViewType.ALL, page_token=t))
    live = [r.info.run_id for r in runs if r.info.lifecycle_stage != LifecycleStage.DELETED]
    if live:
        raise RuntimeError(f"experiment {eid} still has live runs {live}")
    if list(store.search_logged_models(experiment_ids=[eid])):
        raise RuntimeError(f"experiment {eid} still has live logged models")
    run_ids = [r.info.run_id for r in runs]
    _gc_tracking_resources(store, run_ids=run_ids, experiment_ids=[eid], logged_model_ids=list(logged_model_ids),
                           older_than=None, time_delta=0, skip_experiments=False, skip_logged_models=False)
    return len(run_ids)


def run(store) -> int:
    experiments = collectable(store)
    if not experiments:
        logger.info("no deleted tenant's experiments to collect")
        return 0
    models = deleted_logged_models(store)
    failed = 0
    for experiment in experiments:
        try:
            runs = collect(store, experiment, models.get(experiment.experiment_id, []))
            logger.info("collected experiment %s (%s): %d run(s), %d logged model(s)", experiment.experiment_id,
                        experiment.name, runs, len(models.get(experiment.experiment_id, [])))
        except Exception:  # noqa: BLE001 - one experiment's failure must not stop the others
            logger.exception("experiment %s (%s) not collected; the next run retries it",
                             experiment.experiment_id, experiment.name)
            failed += 1
    return 1 if failed else 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if not os.environ.get("MLFLOW_TRACKING_URI", "").startswith(("http://", "https://")):
        # Run artifacts are addressed as mlflow-artifacts:, which only the tracking server resolves.
        logger.error("MLFLOW_TRACKING_URI must name the tracking server")
        return 1
    return run(SqlAlchemyStore(os.environ["BACKEND_STORE_URI"], "mlflow-artifacts:/"))


if __name__ == "__main__":
    sys.exit(main())
