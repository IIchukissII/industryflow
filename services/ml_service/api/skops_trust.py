# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Which types a model's skops file may deserialise into is decided here, by provenance (ADR-0027 dec 13).

skops 0.16 stopped trusting the internal tree structure every tree-based sklearn estimator is built
from: a crafted file can set its node references to values sklearn follows without bounds checks. A
loader must now name the type as trusted, or the file is refused.

Two facts shape this module:

  * **The grant follows provenance.** A kernel-authored model (ADR-0030 dec 8) gets an explicit,
    minimal list: its author already had code execution in their own contained kernel, so the grant
    gives them nothing new. An uploaded model gets no grant, so a tree-based upload is refused at the
    deployment gate's load probe.
  * **The artifact never grants itself trust.** MLflow's sklearn flavor reads `skops_trusted_types`
    from the artifact's own MLmodel and passes it to skops. Left alone, the file under judgement would
    decide what it is trusted with. Every skops load here rewrites that field to the serving side's
    answer before loading, whatever the artifact said.
"""

from __future__ import annotations

import os
import tempfile
from typing import Any, Optional

import uploaded_model

# The internal types the supported tree-based estimators need, and nothing else. Derived from
# skops.io.get_untrusted_types on DecisionTree*, RandomForest*, ExtraTrees*, GradientBoosting*,
# IsolationForest and HistGradientBoosting* models. It grows only by amending ADR-0027 dec 13.
KERNEL_TRUSTED_TYPES: tuple[str, ...] = (
    "sklearn.tree._tree.Tree",
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
)

_SKLEARN_FLAVOR = "sklearn"
_SKOPS = "skops"
_TRUST_KEY = "skops_trusted_types"


def trusted_types_for(provenance: Optional[str]) -> Optional[list[str]]:
    """The serving side's grant for a model of this provenance. None means skops' defaults only."""
    if provenance == uploaded_model.PROVENANCE_KERNEL:
        return list(KERNEL_TRUSTED_TYPES)
    return None


def apply_trust(flavors: dict, provenance: Optional[str]) -> bool:
    """Replace whatever trust list the artifact declares with the serving side's grant.

    Returns True when the model is a skops-serialised sklearn model, i.e. when the rewrite matters.
    """
    conf = flavors.get(_SKLEARN_FLAVOR)
    if not conf or conf.get("serialization_format") != _SKOPS:
        return False
    conf.pop(_TRUST_KEY, None)
    grant = trusted_types_for(provenance)
    if grant:
        conf[_TRUST_KEY] = grant
    return True


def load_pyfunc(model_uri: str, provenance: Optional[str]) -> Any:
    """Load a model through mlflow's pyfunc loader with the trust decided here, not by the artifact.

    A model that is not a skops sklearn model is loaded unchanged. A skops sklearn model is downloaded,
    its MLmodel trust field rewritten, and loaded from the local copy. The sklearn flavor deserialises
    eagerly, so the local copy is not needed once the load returns.
    """
    import mlflow
    import mlflow.artifacts
    from mlflow.models import Model

    meta = Model.load(model_uri)
    if not apply_trust(dict(meta.flavors), provenance):
        return mlflow.pyfunc.load_model(model_uri)

    with tempfile.TemporaryDirectory(prefix="model-") as tmp:
        local = mlflow.artifacts.download_artifacts(artifact_uri=model_uri, dst_path=tmp)
        local_meta = Model.load(local)
        apply_trust(local_meta.flavors, provenance)
        local_meta.save(os.path.join(local, "MLmodel"))
        return mlflow.pyfunc.load_model(local)


def is_untrusted_types_error(exc: BaseException) -> bool:
    """True if skops refused the file for untrusted types, however a caller wrapped that refusal.

    MLflow 3.15 lets skops' exception through; 3.17 re-raises it inside an MlflowException. Matching
    the outer type alone misreports a trust refusal as a generic load failure.
    """
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ == "UntrustedTypesFoundException":
            return True
        cur = cur.__cause__ or cur.__context__
    return "untrusted types" in str(exc).lower()
