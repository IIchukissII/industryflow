# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""skops trust is granted by provenance on the serving side, never by the artifact (ADR-0027 dec 13).

Real mlflow sklearn artifacts, saved to a temp dir and loaded through the serving loader; no mocks on
the load path. The artifacts are saved WITH a trust list in their MLmodel, the way a kernel logs a
tree model, so every refusal below also proves the artifact's own list was discarded.
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


def test_an_uploads_declared_list_is_discarded():
    flavors = {"sklearn": {"serialization_format": "skops", "skops_trusted_types": ["builtins.eval"]}}
    assert skops_trust.apply_trust(flavors, UPLOADED) is True
    assert "skops_trusted_types" not in flavors["sklearn"]


def test_a_kernel_models_declared_list_is_replaced_not_merged():
    flavors = {"sklearn": {"serialization_format": "skops", "skops_trusted_types": ["builtins.eval"]}}
    skops_trust.apply_trust(flavors, KERNEL)
    assert flavors["sklearn"]["skops_trusted_types"] == list(skops_trust.KERNEL_TRUSTED_TYPES)


@pytest.mark.parametrize("flavors", [
    {"xgboost": {"model_format": "ubj"}},
    {"sklearn": {"serialization_format": "cloudpickle"}},
])
def test_other_formats_are_left_alone(flavors):
    before = json.dumps(flavors, sort_keys=True)
    assert skops_trust.apply_trust(flavors, KERNEL) is False
    assert json.dumps(flavors, sort_keys=True) == before


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


def test_an_upload_that_needs_no_grant_still_loads(tmp_path):
    model = LinearRegression().fit(X, Y)
    path = _save(tmp_path, "lin", model)
    assert np.allclose(skops_trust.load_pyfunc(path, UPLOADED).predict(X), model.predict(X))


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
