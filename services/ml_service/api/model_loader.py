# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Where a registered model's artifact is, whether this tenant may use it, and how it is loaded.

A model record addresses its artifact in one of two ways (ADR-0030 dec 8): a kernel-authored model by
the run it was logged in, an uploaded one by the location the upload gateway admitted it to. Both are
values a caller supplied at registration, and the serving side reads them with credentials that reach
every tenant's artifacts. So before anything is loaded — at registration, at deployment, and again on
every load, warm-cache hits included — the address is held to the caller's tenant:

  * a run must belong to an experiment in the tenant's namespace (ADR-0013: the gateway qualifies
    every experiment name with ``tenant_<uuid>.``, so ownership is read off the experiment's name);
  * an artifact location must be exactly an admitted upload under the tenant's own prefix
    (``uploaded_model.admitted_artifact_refusal``).

Checked again at load time, not only at the gates — the shape on every load (``check_address``), the
run's ownership on every cold load — because a row registered before these checks
existed carries whatever it was given, and the deployment gate is not the only way into a serving
path (ADR-0027 dec 10 keeps already-deployed models deployed).

Loading itself is ``skops_trust.load_pyfunc`` with the record's provenance — the same call the
deployment gate's load probe makes, so what is served is loaded the way the probe proved it loads.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import config
import mlflow_namespace
import skops_trust
import uploaded_model
from model_uri import is_run_id, resolve_model_uri

logger = logging.getLogger(__name__)


class ModelNotAdmissible(Exception):
    """The record's address is not one this tenant may load. A refusal, never a transient fault."""


class TrackingStoreUnavailable(Exception):
    """Ownership could not be established because the tracking store did not answer. Transient."""


def _run_owned_by(mlflow_run_id: str, company_id: str) -> bool:
    import mlflow  # imported lazily; see model_compat for why
    from mlflow.exceptions import MlflowException

    client = mlflow.MlflowClient()
    try:
        run = client.get_run(mlflow_run_id)
        experiment = client.get_experiment(run.info.experiment_id)
    except MlflowException as exc:
        if getattr(exc, "error_code", "") == "RESOURCE_DOES_NOT_EXIST":
            return False
        raise TrackingStoreUnavailable(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — a store we cannot reach proves nothing either way
        raise TrackingStoreUnavailable(str(exc)) from exc

    prefix = mlflow_namespace.tenant_prefix(company_id)
    return mlflow_namespace.strip_owned(prefix, getattr(experiment, "name", "") or "") is not None


def check_address(*, company_id: str, mlflow_run_id: Optional[str], artifact_uri: Optional[str]) -> None:
    """Raise ModelNotAdmissible unless the record's address has an admissible shape for this tenant.

    The cheap half of ``check_admissible``: no IO, so it runs on every load, warm-cache hits
    included (ADR-0030 rev 4 dec 8), and a tightened policy — a different uploads bucket, a stricter
    upload id — refuses an already-cached model at once rather than at its next cold load. A run's
    ownership is the half that asks the tracking store; it is proven on the cold load, and the cache
    key carries the tenant and run it was proven for.
    """
    if mlflow_run_id and artifact_uri:
        # A run already says where its model is. A second address beside it is one nothing would
        # read — or one something might, later, without the check the run was given.
        raise ModelNotAdmissible(
            "a model is addressed by the run it was logged in or by the artifact it was uploaded "
            "as, never both"
        )

    if mlflow_run_id:
        if not is_run_id(mlflow_run_id):
            raise ModelNotAdmissible("the run id is not an MLflow run id")
        return

    reason = uploaded_model.admitted_artifact_refusal(
        artifact_uri, company_id, config.config.UPLOAD_ARTIFACT_BUCKET,
    )
    if reason:
        raise ModelNotAdmissible(reason)


def check_admissible(*, company_id: str, mlflow_run_id: Optional[str], artifact_uri: Optional[str]) -> None:
    """Raise ModelNotAdmissible unless this tenant may load the artifact this record addresses.

    Blocking (it may ask the tracking store); call it off the event loop.
    """
    check_address(company_id=company_id, mlflow_run_id=mlflow_run_id, artifact_uri=artifact_uri)
    if mlflow_run_id:
        if not _run_owned_by(mlflow_run_id, company_id):
            # One answer for "no such run" and "another tenant's run": telling them apart would let a
            # caller probe for the existence of runs it does not own.
            raise ModelNotAdmissible("no run with this id exists in your tenant's experiments")


def model_uri_for(*, mlflow_run_id: Optional[str], artifact_uri: Optional[str]) -> str:
    """The URI the artifact is read from: the run's logged model, or the admitted upload itself."""
    if mlflow_run_id:
        return resolve_model_uri(mlflow_run_id)
    if artifact_uri:
        return artifact_uri
    raise ModelNotAdmissible("the model has neither a run nor an artifact location")


def load(*, company_id: str, mlflow_run_id: Optional[str], artifact_uri: Optional[str],
         provenance: Optional[str]) -> Any:
    """Check the address, then load the model with the trust its provenance is granted.

    Blocking (tracking-store and object-store IO); call it off the event loop.
    """
    check_admissible(company_id=company_id, mlflow_run_id=mlflow_run_id, artifact_uri=artifact_uri)
    uri = model_uri_for(mlflow_run_id=mlflow_run_id, artifact_uri=artifact_uri)
    model = skops_trust.load_pyfunc(uri, provenance)
    logger.info(f"Model loaded: {uri} (provenance {provenance})")
    return model


def cache_key(*, company_id: str, mlflow_run_id: Optional[str], artifact_uri: Optional[str],
              provenance: Optional[str]) -> str:
    """The warm-cache key for a record. The tenant is part of it so one tenant's admission check is
    never what serves another, and the provenance because it decides what the load was trusted with."""
    return f"{company_id}|{provenance}|{mlflow_run_id or artifact_uri}"
