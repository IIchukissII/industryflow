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
    decide what it is trusted with. So a skops sklearn model is never loaded through that flavor: it is
    deserialised here, with `skops.io.load` and the serving side's grant, and wrapped in MLflow's own
    pyfunc so callers see the same object. This is also why MLflow's pickle switch is not involved:
    since 3.17 MLflow gates skops behind it precisely because the flavor trusts the artifact's list,
    and the upload path keeps that switch off (ADR-0030 dec 4). Pickle formats still go through MLflow
    and stay behind the switch.
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


def trusted_types_for(provenance: Optional[str]) -> Optional[list[str]]:
    """The serving side's grant for a model of this provenance. None means skops' defaults only."""
    if provenance == uploaded_model.PROVENANCE_KERNEL:
        return list(KERNEL_TRUSTED_TYPES)
    return None


def is_skops_sklearn(flavors: dict) -> bool:
    """True when the model is a skops-serialised sklearn model, the case this module loads itself."""
    conf = flavors.get(_SKLEARN_FLAVOR)
    return bool(conf) and conf.get("serialization_format") == _SKOPS


# The pyfunc entry points a sklearn model may name: MLflow's own set. The name comes from the
# artifact, so it is checked rather than looked up blindly on the wrapper.
_PREDICT_FNS = frozenset({"predict", "predict_proba", "predict_log_proba", "predict_joint_log_proba", "score"})


def _load_local_skops(local: str, provenance: Optional[str]) -> Any:
    import skops.io
    from mlflow.models import Model
    from mlflow.pyfunc import PyFuncModel
    from mlflow.sklearn import _SklearnModelWrapper

    meta = Model.load(os.path.join(local, "MLmodel"))
    py = meta.flavors.get("python_function")
    if not py:
        # MLflow's pyfunc loader refuses a model without this flavor; so does this one.
        raise ValueError("the model has no python_function flavor")
    if py.get("loader_module") != "mlflow.sklearn":
        raise ValueError(f"a sklearn model whose pyfunc loader is {py.get('loader_module')!r} is not supported")
    predict_fn = py.get("predict_fn", "predict")
    if predict_fn not in _PREDICT_FNS:
        raise ValueError(f"unsupported pyfunc predict_fn {predict_fn!r}")

    # The file MLflow's pyfunc loader would open (`data`, else `model_path`), so what is proven here
    # is what any MLflow reader of this artifact would load. It comes from the artifact: keep it
    # inside the downloaded model.
    root = os.path.realpath(local)
    path = os.path.realpath(os.path.join(root, py.get("data") or py.get("model_path") or ""))
    if os.path.commonpath([root, path]) != root or not os.path.isfile(path):
        raise ValueError("the model's serialized file is missing or outside the model directory")

    estimator = skops.io.load(path, trusted=trusted_types_for(provenance))
    return PyFuncModel(
        model_meta=meta,
        model_impl=_SklearnModelWrapper(estimator),
        predict_fn=predict_fn,
        model_id=meta.model_id,
    )


def load_pyfunc(model_uri: str, provenance: Optional[str]) -> Any:
    """Load a model with the trust decided here, never by the artifact.

    A model that is not a skops sklearn model goes through MLflow's pyfunc loader unchanged. A skops
    sklearn model is downloaded and deserialised by `_load_local_skops`; the trust list in its MLmodel
    is never read. Deserialisation is eager, so the local copy is not needed once the load returns.
    """
    import mlflow
    import mlflow.artifacts
    from mlflow.models import Model

    if not is_skops_sklearn(Model.load(model_uri).flavors):
        return mlflow.pyfunc.load_model(model_uri)

    with tempfile.TemporaryDirectory(prefix="model-") as tmp:
        local = mlflow.artifacts.download_artifacts(artifact_uri=model_uri, dst_path=tmp)
        return _load_local_skops(local, provenance)


def is_untrusted_types_error(exc: BaseException) -> bool:
    """True if skops refused the file for untrusted types, however a caller wrapped that refusal.

    Skops raises it directly here, but a caller may wrap it; matching the outer type alone would
    misreport a trust refusal as a generic load failure.
    """
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if type(cur).__name__ == "UntrustedTypesFoundException":
            return True
        cur = cur.__cause__ or cur.__context__
    return "untrusted types" in str(exc).lower()
