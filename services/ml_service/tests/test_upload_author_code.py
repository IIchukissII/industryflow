# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
An uploaded model runs no code its author chose — at registration, at deployment and on every load
(ADR-0030 dec 4 rev 4), and the serving process never imports a module an artifact names.

Each proof uses a real artifact directory whose manifest was edited the way an uploader could edit
it, and a tripwire that records whether the author's choice ran:

  * a loader module that is importable here and writes a marker file when imported;
  * a statsmodels-flavor artifact whose pickle writes a marker file when unpickled.
"""
import os
import pickle
import sys
import threading
import time
import uuid

import numpy as np
import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import extensions  # noqa: E402
import extensions.builtins_detectors  # noqa: F401,E402  (registers the built-ins)
import load_probe  # noqa: E402
import model_compat  # noqa: E402
import model_loader  # noqa: E402
import skops_trust  # noqa: E402
from routers.models import router, get_company_id_dependency  # noqa: E402

CID = str(uuid.uuid4())
TRIPWIRE = "if_tripwire_loader"
SWITCH = "MLFLOW_ALLOW_PICKLE_DESERIALIZATION"


# --- artifacts ----------------------------------------------------------------------------------

def _edit_manifest(path, edit):
    mlmodel = os.path.join(path, "MLmodel")
    with open(mlmodel) as fh:
        manifest = yaml.safe_load(fh)
    edit(manifest)
    with open(mlmodel, "w") as fh:
        yaml.safe_dump(manifest, fh)


def _sklearn_model(path):
    import mlflow.sklearn
    from mlflow.models import infer_signature
    from sklearn.linear_model import LinearRegression

    X = np.random.RandomState(0).rand(20, 2)
    model = LinearRegression().fit(X, X[:, 0])
    mlflow.sklearn.save_model(model, path, serialization_format="skops",
                              signature=infer_signature(X, model.predict(X)))
    return path


@pytest.fixture
def tripwire(tmp_path, monkeypatch):
    """A module importable here that, when imported, writes a marker. Returns the marker path."""
    marker = tmp_path / "imported"
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / f"{TRIPWIRE}.py").write_text(
        f"open({str(marker)!r}, 'w').write('imported')\n"
        "def _load_pyfunc(path):\n    raise RuntimeError('author loader ran')\n"
    )
    monkeypatch.syspath_prepend(str(lib))
    sys.modules.pop(TRIPWIRE, None)
    yield marker
    sys.modules.pop(TRIPWIRE, None)


@pytest.fixture
def tripwire_model(tmp_path, tripwire):
    """A real skops sklearn artifact whose manifest names the tripwire as its pyfunc loader."""
    path = _sklearn_model(str(tmp_path / "m"))
    _edit_manifest(path, lambda m: m["flavors"]["python_function"].update(loader_module=TRIPWIRE))
    return path, tripwire


class _Unpickled:
    """Unpickling this creates the marker file: the proof that a pickle was deserialised."""

    def __init__(self, marker):
        self.marker = marker

    def __reduce__(self):
        return (open, (self.marker, "w"))


@pytest.fixture
def statsmodels_model(tmp_path):
    """A real mlflow.statsmodels artifact whose pickle is replaced by one that creates a marker."""
    import mlflow.statsmodels
    import statsmodels.api as sm

    X = sm.add_constant(np.random.RandomState(0).rand(20, 1))
    results = sm.OLS(X[:, 1] * 2.0, X).fit()
    path = str(tmp_path / "sm")
    mlflow.statsmodels.save_model(results, path)
    with open(os.path.join(path, "MLmodel")) as fh:
        data = yaml.safe_load(fh)["flavors"]["statsmodels"]["data"]
    marker = tmp_path / "unpickled"
    with open(os.path.join(path, data), "wb") as fh:
        pickle.dump(_Unpickled(str(marker)), fh)
    return path, marker


# --- the rule, on the manifest ------------------------------------------------------------------

def test_a_statsmodels_flavor_upload_is_refused_by_name():
    flavors = {"python_function": {"loader_module": "mlflow.statsmodels"},
               "statsmodels": {"data": "model.statsmodels"}}
    with pytest.raises(skops_trust.UnsafeArtifact):
        skops_trust.refuse_author_code(flavors)


@pytest.mark.parametrize("loader", [
    "mlflow.statsmodels", "mlflow.pmdarima", "mlflow.prophet", "mlflow.lightgbm", "mlflow.pyfunc",
    "mlflow.pyfunc.model", "mlflow.sklearn_extra", None, "",
])
def test_every_loader_outside_the_supported_set_is_refused(loader):
    """Deny by default: not a list of known-bad loaders, a list of the platform's own."""
    with pytest.raises(skops_trust.UnsafeArtifact):
        skops_trust.refuse_author_code({"python_function": {"loader_module": loader}})


def test_code_in_any_flavor_is_refused():
    flavors = {"python_function": {"loader_module": "mlflow.sklearn"},
               "sklearn": {"serialization_format": "skops", "code": "code"}}
    with pytest.raises(skops_trust.UnsafeArtifact):
        skops_trust.refuse_author_code(flavors)


def test_an_xgboost_upload_without_its_flavor_is_refused():
    with pytest.raises(skops_trust.UnsafeArtifact):
        skops_trust.refuse_author_code({"python_function": {"loader_module": "mlflow.xgboost"}})


# --- the compatibility gate decides the flavor by name ------------------------------------------

def test_the_compatibility_gate_never_imports_the_loader_an_artifact_names(tripwire_model):
    path, marker = tripwire_model
    result = model_compat.evaluate(path)
    assert not marker.exists(), "the gate imported a module the artifact named"
    assert TRIPWIRE not in sys.modules
    assert result.status == model_compat.INCOMPATIBLE
    assert result.flavor_supported is False


def test_a_flavor_a_loaded_detector_claims_is_in_the_supported_set(monkeypatch):
    monkeypatch.setattr(model_compat, "_read_declarations", lambda uri: ("mlflow.catboost", {}))
    monkeypatch.setattr(extensions, "detector_capabilities",
                        lambda: [{"name": "cb", "handles_flavors": ["mlflow.catboost"]}])
    assert model_compat.evaluate("x").flavor_supported is True


# --- at the wire --------------------------------------------------------------------------------

class FakeRepo:
    def __init__(self):
        self.created = []
        self.row = None
        self.status_updates = []

    async def create_model(self, company_id, model_data):
        self.created.append(model_data)
        return uuid.uuid4()

    async def get_model_by_id(self, company_id, model_id):
        return self.row

    async def update_model_compatibility(self, **kw):
        return True

    async def update_model_status(self, company_id, model_id, status):
        self.status_updates.append(status)
        return True


@pytest.fixture
def client(monkeypatch):
    """The real gates over a local artifact directory. Only the address rule is admitted here (it has
    its own suite, test_model_admission): the manifest read and the compatibility gate are real."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_company_id_dependency] = lambda: CID
    app.state.ml_repository = FakeRepo()
    monkeypatch.setattr(model_loader, "check_admissible", lambda **kw: None)
    c = TestClient(app)
    c.repo = app.state.ml_repository
    return c


def _register(client, path):
    return client.post("/api/models", json={
        "model_name": "m", "model_version": "1", "model_type": "outlier",
        "artifact_uri": path, "score_semantics": "outlier_score",
    })


def test_registration_refuses_an_upload_naming_its_own_loader_without_importing_it(client, tripwire_model):
    path, marker = tripwire_model
    r = _register(client, path)
    assert not marker.exists(), "registration imported a module the artifact named"
    assert TRIPWIRE not in sys.modules
    assert r.status_code == 422, r.text
    assert "ADR-0030 dec 4" in str(r.json()["detail"]), "refused, but not by the author-code check"
    assert client.repo.created == []


def test_registration_refuses_a_statsmodels_upload(client, statsmodels_model):
    path, marker = statsmodels_model
    r = _register(client, path)
    assert r.status_code == 422, r.text
    assert "ADR-0030 dec 4" in str(r.json()["detail"])
    assert not marker.exists()
    assert client.repo.created == []


def test_deployment_refuses_an_upload_naming_its_own_loader_before_the_probe(client, tripwire_model, monkeypatch):
    path, marker = tripwire_model
    probed = []

    async def probe(uri):
        probed.append(uri)
        return load_probe.ProbeResult(True, "score")

    monkeypatch.setattr(load_probe, "probe", probe)
    client.repo.row = {"model_name": "m", "artifact_uri": path, "provenance": "uploaded",
                       "detector": "outlier"}
    r = client.post("/api/models/m1/deploy", json={"model_id": "m1", "environment": "production"})
    assert r.status_code == 409, r.text
    assert "ADR-0030 dec 4" in str(r.json()["detail"])
    assert not marker.exists(), "deployment imported a module the artifact named"
    assert probed == []
    assert client.repo.status_updates == []


def test_a_safe_upload_still_registers(client, tmp_path):
    r = _register(client, _sklearn_model(str(tmp_path / "ok")))
    assert r.status_code == 201, r.text


# --- every load ---------------------------------------------------------------------------------

def _refused(load):
    """Whether `load` was refused as unsafe — without stopping a test from checking the tripwire."""
    try:
        load()
    except skops_trust.UnsafeArtifact:
        return True
    except Exception:  # noqa: BLE001 — failing some other way is not a refusal
        return False
    return False


def test_loading_a_statsmodels_upload_never_unpickles_it(statsmodels_model):
    path, marker = statsmodels_model
    refused = _refused(lambda: skops_trust.load_pyfunc(path, "uploaded"))
    assert not marker.exists(), "an uploaded model's pickle was deserialised in the serving process"
    assert refused


def test_loading_an_upload_naming_its_own_loader_never_imports_it(tripwire_model):
    path, marker = tripwire_model
    refused = _refused(lambda: skops_trust.load_pyfunc(path, "uploaded"))
    assert not marker.exists(), "loading imported a module the artifact named"
    assert refused


# --- the pickle switch, in the serving process ---------------------------------------------------

def _record_switch(monkeypatch, delay=0.0):
    import mlflow.pyfunc

    seen = []

    def load_model(uri, *a, **kw):
        seen.append(os.environ.get(SWITCH))
        time.sleep(delay)
        return object()

    monkeypatch.setattr(mlflow.pyfunc, "load_model", load_model)
    return seen


@pytest.fixture
def xgboost_upload(tmp_path):
    """A manifest an upload may have: the xgboost native booster. Loading is stubbed per test."""
    path = tmp_path / "xgb"
    path.mkdir()
    (path / "MLmodel").write_text(yaml.safe_dump({"flavors": {
        "python_function": {"loader_module": "mlflow.xgboost", "data": "model.ubj"},
        "xgboost": {"model_class": "xgboost.core.Booster", "data": "model.ubj"},
    }}))
    (path / "model.ubj").write_bytes(b"")
    return str(path)


@pytest.mark.parametrize("configured", [None, "true"])
def test_the_switch_is_off_for_an_uploaded_load_and_restored_after(monkeypatch, xgboost_upload, configured):
    if configured is None:
        monkeypatch.delenv(SWITCH, raising=False)
    else:
        monkeypatch.setenv(SWITCH, configured)
    seen = _record_switch(monkeypatch)
    skops_trust.load_pyfunc(xgboost_upload, "uploaded")
    assert seen == ["false"]
    assert os.environ.get(SWITCH) == configured


def test_a_kernel_load_reads_the_switch_as_configured(monkeypatch, xgboost_upload):
    monkeypatch.setenv(SWITCH, "true")
    seen = _record_switch(monkeypatch)
    skops_trust.load_pyfunc(xgboost_upload, "kernel")
    assert seen == ["true"]


def test_an_uploaded_load_never_turns_the_switch_off_under_a_kernel_load(monkeypatch, xgboost_upload):
    """Both run on worker threads in one process. A kernel load in flight while an upload loads must
    still read the switch as configured, before and after the upload takes it."""
    monkeypatch.setenv(SWITCH, "true")
    import mlflow.pyfunc

    seen = {"kernel": [], "upload": []}
    kernel_started = threading.Event()

    def load_model(uri, *a, **kw):
        if threading.current_thread().name == "kernel":
            kernel_started.set()
            seen["kernel"].append(os.environ.get(SWITCH))
            time.sleep(0.3)
            seen["kernel"].append(os.environ.get(SWITCH))
        else:
            seen["upload"].append(os.environ.get(SWITCH))
        return object()

    monkeypatch.setattr(mlflow.pyfunc, "load_model", load_model)
    kernel = threading.Thread(name="kernel", target=skops_trust.load_pyfunc, args=(xgboost_upload, "kernel"))
    kernel.start()
    kernel_started.wait(5)
    skops_trust.load_pyfunc(xgboost_upload, "uploaded")
    kernel.join(5)
    assert seen["kernel"] == ["true", "true"]
    assert seen["upload"] == ["false"]
    assert os.environ.get(SWITCH) == "true"


def test_an_upload_naming_a_file_outside_itself_is_refused(tmp_path):
    path = tmp_path / "xgb"
    path.mkdir()
    (path / "MLmodel").write_text(yaml.safe_dump({"flavors": {
        "python_function": {"loader_module": "mlflow.xgboost", "data": "../elsewhere"},
        "xgboost": {"data": "../elsewhere"},
    }}))
    with pytest.raises(skops_trust.UnsafeArtifact):
        skops_trust.load_pyfunc(str(path), "uploaded")

