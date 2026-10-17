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
    and the upload path keeps that switch off (ADR-0030 dec 4) — in the probe's process and, for the
    duration of an uploaded load, in the serving process. Pickle formats still go through MLflow and
    stay behind the switch; an upload is refused before it reaches one (`refuse_author_code`).
"""

from __future__ import annotations

import os
import tempfile
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

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


class UnsafeArtifact(ValueError):
    """An uploaded artifact whose load path would run or deserialise something its author chose."""


# The loaders an UPLOADED model may name: the platform's own loaders that read the model as data
# (ADR-0030 dec 4 rev 4). Deny by default: any other loader is refused by name, without being
# imported — an arbitrary module, MLflow's pyfunc `python_model` loader, or an MLflow flavor whose
# format is a pickle (statsmodels and most others). Adding one here claims it never deserialises
# author objects nor runs author code.
#
#   * mlflow.sklearn — skops format only, loaded by `_load_local_skops` under the serving side's grant;
#   * mlflow.xgboost — the native booster formats, constructing only `_XGBOOST_MODEL_CLASSES`.
UPLOADED_LOADERS = frozenset({"mlflow.sklearn", "mlflow.xgboost"})

# The classes the xgboost flavor may instantiate for an uploaded model. MLflow builds the model by
# instantiating whatever class path the artifact's MLmodel names, so for an artifact nothing watched
# being made that name is an author's choice of what to construct — it is held to xgboost's own models.
_XGBOOST_MODEL_CLASSES = frozenset({
    "xgboost.core.Booster",
    "xgboost.sklearn.XGBClassifier",
    "xgboost.sklearn.XGBRegressor",
    "xgboost.sklearn.XGBRanker",
    "xgboost.sklearn.XGBRFClassifier",
    "xgboost.sklearn.XGBRFRegressor",
})


def refuse_author_code(flavors: dict) -> None:
    """Raise UnsafeArtifact if loading this UPLOADED artifact would run author-chosen code.

    The upload gateway refuses such an artifact before it is admitted (ADR-0030 dec 4). This is the
    serving side's own check of the same property, run at registration, at deployment and before every
    load (dec 4 rev 4): the bytes are loaded by a process that holds credentials for every tenant, and
    they may have been replaced since they were judged. It reads only the manifest and imports nothing
    the manifest names, so it is safe to run in the serving process itself.

      * ``code`` in any flavor is a directory the artifact asks to put on ``sys.path``;
      * a loader outside ``UPLOADED_LOADERS`` is refused by name — deny by default, never imported to
        find out what it is (a pyfunc ``python_model``, a pickle-format flavor, any other module);
      * a sklearn model not serialised with skops is object deserialisation;
      * the xgboost flavor instantiates a class the artifact names.
    """
    py = flavors.get("python_function") or {}
    if not py:
        raise UnsafeArtifact("the model has no python_function flavor")
    if any(isinstance(conf, dict) and conf.get("code") for conf in flavors.values()):
        raise UnsafeArtifact("the artifact ships code to put on the import path")
    loader = py.get("loader_module")
    if loader not in UPLOADED_LOADERS:
        raise UnsafeArtifact(
            f"the artifact's loader {loader!r} is not one of the platform's own data-only loaders "
            f"for uploaded models ({', '.join(sorted(UPLOADED_LOADERS))})"
        )
    if loader == "mlflow.sklearn" and not is_skops_sklearn(flavors):
        raise UnsafeArtifact("a sklearn model not serialised with skops is object deserialisation")
    if loader == "mlflow.xgboost":
        conf = flavors.get("xgboost")
        if not isinstance(conf, dict):
            raise UnsafeArtifact("an xgboost-loaded model has no xgboost flavor")
        model_class = conf.get("model_class", "xgboost.core.Booster")
        if model_class not in _XGBOOST_MODEL_CLASSES:
            raise UnsafeArtifact(f"the artifact asks to construct {model_class!r}, not an xgboost model")


def read_flavors(model_uri: str) -> dict:
    """The flavors an artifact's manifest declares. Reads the MLmodel file only; imports and loads
    nothing it names."""
    from mlflow.models import Model

    return Model.load(model_uri).flavors


def _refuse_paths_outside(local: str, flavors: dict) -> None:
    """The files the loaders open are named by the manifest; for an upload they stay inside it."""
    root = os.path.realpath(local)
    for conf in (flavors.get("python_function") or {}, flavors.get("xgboost") or {}):
        rel = conf.get("data") if isinstance(conf, dict) else None
        if rel is None:
            continue
        path = os.path.realpath(os.path.join(root, str(rel)))
        if os.path.commonpath([root, path]) != root:
            raise UnsafeArtifact("the artifact names a file outside the model directory")


# MLflow's switch for deserialising executable formats (pickle and its relatives). It defaults to on,
# and MLflow reads it from the process environment at every load. ADR-0030 dec 4 keeps it off for
# uploaded models; ADR-0027 defers that decision for kernel-authored ones, so a kernel load must see it
# as the deployment configured it. Both kinds of load run in this process on worker threads, so the
# switch is turned off only while no other load reads it: an uploaded load holds `_PICKLE_SWITCH`
# exclusively, every other MLflow load holds it shared.
_PICKLE_SWITCH_ENV = "MLFLOW_ALLOW_PICKLE_DESERIALIZATION"


class _SwitchLock:
    """A readers-writer lock over the switch: many loads that read it as configured, or one load that
    turns it off. A waiting writer blocks new readers, so kernel loads cannot starve an upload."""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._writers_waiting = 0

    @contextmanager
    def as_configured(self) -> Iterator[None]:
        with self._cond:
            while self._writer or self._writers_waiting:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if not self._readers:
                    self._cond.notify_all()

    @contextmanager
    def off(self) -> Iterator[None]:
        with self._cond:
            self._writers_waiting += 1
            try:
                while self._writer or self._readers:
                    self._cond.wait()
            finally:
                self._writers_waiting -= 1
            self._writer = True
        prior = os.environ.get(_PICKLE_SWITCH_ENV)
        os.environ[_PICKLE_SWITCH_ENV] = "false"
        try:
            yield
        finally:
            if prior is None:
                os.environ.pop(_PICKLE_SWITCH_ENV, None)
            else:
                os.environ[_PICKLE_SWITCH_ENV] = prior
            with self._cond:
                self._writer = False
                self._cond.notify_all()


_PICKLE_SWITCH = _SwitchLock()


def load_pyfunc(model_uri: str, provenance: Optional[str]) -> Any:
    """Load a model with the trust decided here, never by the artifact.

    A model that is not a skops sklearn model goes through MLflow's pyfunc loader unchanged. A skops
    sklearn model is downloaded and deserialised by `_load_local_skops`; the trust list in its MLmodel
    is never read. Deserialisation is eager, so the local copy is not needed once the load returns.

    An uploaded model is first held to `refuse_author_code`, and is downloaded ONCE and loaded from
    that copy: the manifest that was checked is the manifest that is loaded, rather than a second
    fetch of something that may have changed in between. MLflow's pickle switch is off for that load,
    in this process too (`_PICKLE_SWITCH`). This is the one load path for serving and for the
    deployment gate's probe alike, so what is served is loaded the way the probe proved.
    """
    import mlflow
    import mlflow.artifacts
    from mlflow.models import Model

    if provenance == uploaded_model.PROVENANCE_UPLOADED:
        with tempfile.TemporaryDirectory(prefix="model-") as tmp:
            local = mlflow.artifacts.download_artifacts(artifact_uri=model_uri, dst_path=tmp)
            flavors = Model.load(os.path.join(local, "MLmodel")).flavors
            refuse_author_code(flavors)
            _refuse_paths_outside(local, flavors)
            if is_skops_sklearn(flavors):
                return _load_local_skops(local, provenance)
            # The loaders an upload may name (UPLOADED_LOADERS) read eagerly; the copy may go.
            with _PICKLE_SWITCH.off():
                return mlflow.pyfunc.load_model(local)

    with _PICKLE_SWITCH.as_configured():
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
