# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""skops trust is granted by provenance on the serving side, never by the artifact (ADR-0027 dec 13).

Real mlflow sklearn artifacts, saved to a temp dir and loaded through the serving loader; no mocks on
the load path. The artifacts are saved WITH a trust list in their MLmodel, the way a kernel logs a
tree model, so every refusal below also proves the artifact's own list is never read.
"""

import ast
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

API = Path(__file__).resolve().parent.parent / "api"
sys.path.insert(0, str(API))

import mlflow.sklearn  # noqa: E402
from mlflow.models import Model, infer_signature  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor, IsolationForest, RandomForestRegressor  # noqa: E402
from sklearn.linear_model import LinearRegression  # noqa: E402
from skops.io.exceptions import UntrustedTypesFoundException  # noqa: E402

import load_probe  # noqa: E402
import skops_trust  # noqa: E402
import uploaded_model  # noqa: E402

KERNEL = uploaded_model.PROVENANCE_KERNEL
UPLOADED = uploaded_model.PROVENANCE_UPLOADED

X = np.random.RandomState(0).rand(40, 4)
Y = X @ [1.0, 2.0, 3.0, 4.0]


def _save(tmp_path, name, model, declared=None):
    path = str(tmp_path / name)
    out = model.predict(X) if hasattr(model, "predict") else model.score_samples(X)
    mlflow.sklearn.save_model(
        model, path, serialization_format="skops",
        skops_trusted_types=declared, signature=infer_signature(X, out),
    )
    return path


@pytest.fixture
def forest(tmp_path):
    model = RandomForestRegressor(n_estimators=5, random_state=0).fit(X, Y)
    return model, _save(tmp_path, "rf", model, declared=list(skops_trust.KERNEL_TRUSTED_TYPES))


# --- the grant ----------------------------------------------------------------------------------

def test_kernel_models_get_the_minimal_list():
    assert skops_trust.trusted_types_for(KERNEL) == [
        "sklearn.tree._tree.Tree",
        "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
    ]


@pytest.mark.parametrize("provenance", [UPLOADED, None, "anything-else"])
def test_nothing_else_gets_a_grant(provenance):
    assert skops_trust.trusted_types_for(provenance) is None


@pytest.mark.parametrize("flavors,expected", [
    ({"sklearn": {"serialization_format": "skops"}}, True),
    ({"sklearn": {"serialization_format": "cloudpickle"}}, False),
    ({"xgboost": {"model_format": "ubj"}}, False),
])
def test_only_skops_sklearn_models_are_loaded_here(flavors, expected):
    assert skops_trust.is_skops_sklearn(flavors) is expected


# --- real artifacts ------------------------------------------------------------------------------

def test_a_kernel_tree_model_loads_and_scores_identically(forest):
    model, path = forest
    loaded = skops_trust.load_pyfunc(path, KERNEL)
    assert np.abs(loaded.predict(X) - model.predict(X)).max() == 0.0


def test_the_same_tree_model_is_refused_as_an_upload(forest):
    # Its MLmodel declares the tree types as trusted. If this loads, the artifact granted itself trust.
    _, path = forest
    with pytest.raises(UntrustedTypesFoundException):
        skops_trust.load_pyfunc(path, UPLOADED)


def test_loading_never_rewrites_the_stored_artifact(forest):
    _, path = forest
    with pytest.raises(UntrustedTypesFoundException):
        skops_trust.load_pyfunc(path, UPLOADED)
    declared = Model.load(path).flavors["sklearn"]["skops_trusted_types"]
    assert declared == list(skops_trust.KERNEL_TRUSTED_TYPES)


@pytest.mark.parametrize("estimator", [
    IsolationForest(n_estimators=5, random_state=0),
    HistGradientBoostingRegressor(max_iter=5),
])
def test_every_supported_tree_family_loads_for_the_kernel(tmp_path, estimator):
    model = estimator.fit(X) if isinstance(estimator, IsolationForest) else estimator.fit(X, Y)
    path = _save(tmp_path, type(estimator).__name__, model, declared=list(skops_trust.KERNEL_TRUSTED_TYPES))
    skops_trust.load_pyfunc(path, KERNEL)


def test_the_loaded_model_is_the_same_pyfunc_object_callers_use(forest):
    # Detectors reach the estimator through `_model_impl.sklearn_model`; the probe scores against the
    # signature. Loading it ourselves must not change either.
    model, path = forest
    loaded = skops_trust.load_pyfunc(path, KERNEL)
    assert isinstance(loaded, mlflow.pyfunc.PyFuncModel)
    assert type(loaded._model_impl.sklearn_model) is type(model)
    assert loaded.metadata.signature == Model.load(path).signature


def _tamper(path, **pyfunc):
    meta = Model.load(path)
    meta.flavors["python_function"].update(pyfunc)
    meta.save(str(Path(path) / "MLmodel"))


def test_an_artifact_cannot_pick_an_arbitrary_method(tmp_path):
    path = _save(tmp_path, "lin", LinearRegression().fit(X, Y))
    _tamper(path, predict_fn="__class__")
    with pytest.raises(ValueError, match="predict_fn"):
        skops_trust.load_pyfunc(path, UPLOADED)


@pytest.mark.parametrize("field", ["model_path", "data"])
def test_an_artifact_cannot_point_outside_itself(tmp_path, field):
    path = _save(tmp_path, "lin", LinearRegression().fit(X, Y))
    _tamper(path, **{field: "../../etc/passwd"})
    with pytest.raises(ValueError, match="outside the model directory"):
        skops_trust.load_pyfunc(path, UPLOADED)


def test_the_file_loaded_is_the_one_mlflow_would_load(tmp_path):
    # MLflow's pyfunc loader opens python_function's `data`, else its `model_path`; the sklearn
    # flavor's `pickled_model` is not what it reads, so a differing value must not change the load.
    model = LinearRegression().fit(X, Y)
    path = _save(tmp_path, "lin", model)
    meta = Model.load(path)
    meta.flavors["sklearn"]["pickled_model"] = "elsewhere.skops"
    meta.save(str(Path(path) / "MLmodel"))
    assert np.allclose(skops_trust.load_pyfunc(path, UPLOADED).predict(X), model.predict(X))


def test_a_model_without_a_pyfunc_flavor_is_refused(tmp_path):
    path = _save(tmp_path, "lin", LinearRegression().fit(X, Y))
    meta = Model.load(path)
    del meta.flavors["python_function"]
    meta.save(str(Path(path) / "MLmodel"))
    with pytest.raises(ValueError, match="python_function"):
        skops_trust.load_pyfunc(path, UPLOADED)


def test_an_upload_that_needs_no_grant_still_loads(tmp_path):
    model = LinearRegression().fit(X, Y)
    path = _save(tmp_path, "lin", model)
    assert np.allclose(skops_trust.load_pyfunc(path, UPLOADED).predict(X), model.predict(X))


# --- xgboost: read from the checked manifest alone (ADR-0037) -----------------------------------

def _save_xgb(tmp_path, name="xgb"):
    import mlflow.xgboost
    import xgboost

    model = xgboost.XGBRegressor(n_estimators=5, max_depth=2).fit(X, Y)
    path = str(tmp_path / name)
    mlflow.xgboost.save_model(model, path, signature=infer_signature(X, model.predict(X)))
    return model, path


def test_an_xgboost_model_loads_and_scores_identically(tmp_path):
    model, path = _save_xgb(tmp_path)
    assert np.allclose(skops_trust.load_pyfunc(path, UPLOADED).predict(X), model.predict(X))


def test_a_nested_manifest_cannot_choose_what_is_constructed(tmp_path, monkeypatch):
    # MLflow's xgboost loader reads the MLmodel beside the pyfunc `data` file. Pointing `data` into a
    # subdirectory with its own MLmodel used to let that unchecked manifest name the class to build.
    model, path = _save_xgb(tmp_path)
    data = Model.load(path).flavors["xgboost"]["data"]
    inner = Path(path) / "inner"
    inner.mkdir()
    (inner / data).write_bytes((Path(path) / data).read_bytes())
    (inner / "MLmodel").write_text(f"flavors:\n  xgboost:\n    data: {data}\n    model_class: marker.Marker\n")
    (tmp_path / "marker.py").write_text(
        "import pathlib\nclass Marker:\n    def __init__(self):\n"
        f"        pathlib.Path({str(tmp_path / 'constructed')!r}).touch()\n"
        "    def load_model(self, path):\n        pass\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    _tamper(path, data=f"inner/{data}")

    loaded = skops_trust.load_pyfunc(path, KERNEL)
    assert not (tmp_path / "constructed").exists()
    assert np.allclose(loaded.predict(X), model.predict(X))


def test_an_xgboost_artifact_cannot_point_outside_itself(tmp_path):
    _, path = _save_xgb(tmp_path)
    meta = Model.load(path)
    meta.flavors["xgboost"]["data"] = "../../etc/passwd"
    meta.save(str(Path(path) / "MLmodel"))
    with pytest.raises(ValueError, match="outside the model directory"):
        skops_trust.load_pyfunc(path, UPLOADED)


# --- the deployment gate's probe ----------------------------------------------------------------

def test_the_probe_refuses_an_uploaded_tree_model_with_a_trust_reason(forest):
    _, path = forest
    proc = subprocess.run(
        [sys.executable, str(load_probe._WORKER), path],
        capture_output=True, text=True, timeout=120,
        env=load_probe._worker_env(), cwd=str(API),
    )
    verdict = json.loads(proc.stdout.strip().splitlines()[-1])
    assert verdict["ok"] is False
    assert verdict["stage"] == "trust"
    assert "ADR-0027 dec 13" in verdict["error"]


def _probe(path):
    proc = subprocess.run(
        [sys.executable, str(load_probe._WORKER), path],
        capture_output=True, text=True, timeout=120,
        env=load_probe._worker_env(), cwd=str(API),
    )
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_the_probe_scores_an_uploaded_model_that_needs_no_grant(tmp_path):
    # #303: since mlflow 3.17 its sklearn flavor refuses every skops file while the pickle switch is off,
    # which the probe keeps off. Loading skops ourselves keeps linear uploads admissible.
    verdict = _probe(_save(tmp_path, "lin", LinearRegression().fit(X, Y)))
    assert verdict["ok"] is True, verdict


def _probe_with_sample(path, sample):
    proc = subprocess.run(
        [sys.executable, str(load_probe._WORKER), path], input=json.dumps(sample),
        capture_output=True, text=True, timeout=120, env=load_probe._worker_env(), cwd=str(API),
    )
    verdict, *rest = [json.loads(line) for line in proc.stdout.strip().splitlines()]
    assert "ok" in verdict, "the verdict is printed before the sample is scored"
    return {**verdict, **(rest[0] if rest else {})}


def test_the_probe_scores_a_feature_sample_in_its_own_load(tmp_path):
    # ADR-0035 dec 2: an upload's baseline scores come from the probe's load, through the detector.
    from sklearn.linear_model import LogisticRegression

    model = LogisticRegression().fit(X, (Y > Y.mean()).astype(int))
    rows = X[:5].tolist()
    verdict = _probe_with_sample(_save(tmp_path, "logit", model),
                                 {"detector": "sklearn", "context": {}, "rows": rows})
    assert verdict["ok"] is True, verdict
    assert np.allclose(verdict["scores"], model.predict_proba(X[:5])[:, 1])


def test_a_detector_that_cannot_score_the_sample_does_not_fail_the_probe(tmp_path):
    verdict = _probe_with_sample(_save(tmp_path, "lin", LinearRegression().fit(X, Y)),
                                 {"detector": "sklearn", "context": {}, "rows": X[:2].tolist()})
    assert verdict["ok"] is True and "UninterpretableModel" in verdict["sample_error"], verdict


def _schema(kind):
    from mlflow.types import ColSpec, Schema, TensorSpec
    cols = lambda t: Schema([ColSpec(t, f"c{i}") for i in range(4)])  # noqa: E731
    return {
        "double": cols("double"), "float32": cols("float"), "int32": cols("integer"), "int64": cols("long"),
        "tensor": Schema([TensorSpec(np.dtype("float64"), (-1, 4))]),
        "tensor-float32": Schema([TensorSpec(np.dtype("float32"), (-1, 4))]),
        "named-tensor": Schema([TensorSpec(np.dtype("float64"), (-1, 4), "x")]),
    }[kind]


@pytest.mark.parametrize("kind", ["double", "float32", "int32", "int64", "tensor", "tensor-float32"])
def test_the_probe_builds_an_input_in_the_declared_types(tmp_path, kind):
    # A synthetic input of the wrong dtype or form fails MLflow's schema enforcement, so a valid
    # upload would be refused at the score stage.
    from mlflow.models import ModelSignature
    from mlflow.types import ColSpec, Schema
    path = str(tmp_path / kind)
    mlflow.sklearn.save_model(LinearRegression().fit(X, Y), path, serialization_format="skops",
                              signature=ModelSignature(inputs=_schema(kind), outputs=Schema([ColSpec("double")])))
    verdict = _probe(path)
    assert verdict["ok"] is True, verdict


def test_the_probe_feeds_named_tensors_by_name():
    # Named tensors are passed as a dict of arrays; an sklearn model cannot score that form, so this
    # checks the input itself rather than a round trip.
    from mlflow.models import ModelSignature
    import load_probe_worker
    out = load_probe_worker._synthetic_frame(ModelSignature(inputs=_schema("named-tensor")))
    assert set(out) == {"x"} and out["x"].shape == (1, 4) and out["x"].dtype == np.float64


# --- the kernel's copy of the list ---------------------------------------------------------------

def test_the_kernel_client_carries_the_same_list():
    # The kernel needs the list at SAVE time (mlflow test-loads on save); serving ignores it. Two copies
    # drift silently, and a kernel list missing a type makes logging that model fail in every notebook.
    src = (API.parents[2] / "clients" / "python" / "industryflow" / "models.py").read_text()
    tree = ast.parse(src)
    value = next(
        n.value for n in tree.body
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SKOPS_TRUSTED_TYPES"
    )
    assert ast.literal_eval(value) == list(skops_trust.KERNEL_TRUSTED_TYPES)


def _raise_wrapped(implicit: bool):
    try:
        raise UntrustedTypesFoundException(["sklearn.tree._tree.Tree"])
    except UntrustedTypesFoundException as inner:
        if implicit:
            raise RuntimeError("load failed")  # noqa: B904 — __context__ only, as some wrappers do
        raise mlflow.exceptions.MlflowException("wrapped") from inner


@pytest.mark.parametrize("implicit", [False, True])
def test_a_wrapped_untrusted_types_refusal_is_still_a_trust_refusal(implicit):
    # A wrapper that re-raises skops' refusal must not turn a trust refusal into a "load" failure.
    with pytest.raises(Exception) as info:
        _raise_wrapped(implicit)
    assert skops_trust.is_untrusted_types_error(info.value)


def test_an_unrelated_load_error_is_not_a_trust_refusal():
    assert not skops_trust.is_untrusted_types_error(ValueError("bad input shape"))
