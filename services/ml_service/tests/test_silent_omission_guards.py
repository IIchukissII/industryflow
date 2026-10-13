# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Smaller properties of ml_service that each failed silently, one guard apiece.

Most of these are lists that must be remembered and whose omission says nothing: the tenant-creation
function's migration calls, the probe's environment, the image's contents, the path filter that
decides whether the model round trip runs, the bucket named in two deployments. Each test reads both
sides of its list.
"""
import ast
import asyncio
import glob
import os
import re
import sys
import uuid

import pytest
import yaml

API = os.path.join(os.path.dirname(__file__), "..", "api")
ROOT = os.path.join(os.path.dirname(__file__), "..", "..", "..")
sys.path.insert(0, API)

import load_probe  # noqa: E402
import model_compat  # noqa: E402
import model_uri  # noqa: E402
import repository  # noqa: E402
import skops_trust  # noqa: E402


# --- ADR-0003 dec 6: the tenant path is transaction-scoped ----------------------------------------

def _string_constants(path):
    with open(path) as fh:
        tree = ast.parse(fh.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value
        elif isinstance(node, ast.JoinedStr):
            yield "".join(v.value for v in node.values if isinstance(v, ast.Constant))


def test_nothing_in_the_service_sets_a_session_scoped_search_path():
    """A session `SET search_path` stays on the pooled connection for its next borrower, and
    tenant isolation then rests on every later query re-setting it first."""
    offenders = []
    for path in glob.glob(os.path.join(API, "**", "*.py"), recursive=True):
        for value in _string_constants(path):
            if re.match(r"\s*SET\s+search_path", value, re.I):
                offenders.append(os.path.relpath(path, API))
    assert not offenders, f"session-scoped SET search_path in {sorted(set(offenders))}"


class _Tx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        self.conn.depth += 1

    async def __aexit__(self, *exc):
        self.conn.depth -= 1
        return False


class _Conn:
    def __init__(self):
        self.depth = 0
        self.executed = []

    def transaction(self):
        return _Tx(self)

    async def execute(self, sql, *args):
        self.executed.append((sql, args, self.depth))


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *exc):
        return False


class _Pool:
    def __init__(self):
        self.conn = _Conn()

    def acquire(self):
        return _Acquire(self.conn)


def test_the_repository_sets_the_tenant_path_locally_inside_a_transaction():
    pool = _Pool()
    cid = str(uuid.uuid4())

    async def use():
        async with repository.tenant_connection(pool, cid) as conn:
            assert conn.depth == 1, "the block must run inside the transaction that scopes the path"

    asyncio.run(use())
    (sql, args, depth), = pool.conn.executed
    assert "set_config('search_path', $1, true)" in sql
    assert args == (f"{repository.normalize_company_id_to_schema(cid)}, public",)
    assert depth == 1


def test_a_company_id_that_is_not_a_uuid_never_reaches_the_pool():
    pool = _Pool()

    async def use():
        async with repository.tenant_connection(pool, "x; DROP SCHEMA public"):
            pass

    with pytest.raises(ValueError):
        asyncio.run(use())
    assert pool.conn.executed == []


# --- the probe's environment ----------------------------------------------------------------------

@pytest.mark.parametrize("secret", [
    "ML_SERVICE_DB_PASSWORD", "JWT_SECRET_KEY", "INTERNAL_SERVICE_TOKEN", "CAPABILITY_REDIS_URL",
    "DB_HOST", "SOME_SECRET_ADDED_LATER",
])
def test_the_probe_is_not_handed_the_services_secrets(monkeypatch, secret):
    """The probe loads a stranger's artifact. Whatever runs there must not find the database
    password or the token-signing key in its environment — and a secret added later is withheld by
    default, because the environment is an allowlist."""
    monkeypatch.setenv(secret, "s3cr3t")
    assert secret not in load_probe._worker_env()


def test_the_probe_can_still_read_the_artifact_store(monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "MLFLOW_S3_ENDPOINT_URL", "PATH"):
        monkeypatch.setenv(name, "v")
    env = load_probe._worker_env()
    assert all(env.get(n) == "v" for n in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY",
                                           "MLFLOW_S3_ENDPOINT_URL", "PATH"))


# --- an uploaded artifact may not run author code where it is loaded ------------------------------

def _flavors(loader="mlflow.sklearn", **py):
    flavors = {"python_function": {"loader_module": loader, **py}}
    if loader == "mlflow.sklearn":
        flavors["sklearn"] = {"serialization_format": "skops"}
    return flavors


@pytest.mark.parametrize("flavors", [
    _flavors(code="code"),                                    # puts the artifact on sys.path
    _flavors(loader="evil_module"),                           # a module the artifact names
    _flavors(loader="mlflow.pyfunc.model"),                   # a cloudpickled python_model
    {"python_function": {"loader_module": "mlflow.sklearn"},
     "sklearn": {"serialization_format": "cloudpickle"}},     # object deserialisation
    {"python_function": {"loader_module": "mlflow.xgboost"},
     "xgboost": {"model_class": "subprocess.Popen"}},         # a class the artifact names
    {},
])
def test_author_code_is_refused_for_an_upload(flavors):
    with pytest.raises(skops_trust.UnsafeArtifact):
        skops_trust.refuse_author_code(flavors)


@pytest.mark.parametrize("flavors", [
    _flavors(),
    {"python_function": {"loader_module": "mlflow.xgboost"},
     "xgboost": {"model_class": "xgboost.sklearn.XGBClassifier"}},
    {"python_function": {"loader_module": "mlflow.xgboost"}, "xgboost": {}},
])
def test_what_an_upload_may_be_is_admitted(flavors):
    skops_trust.refuse_author_code(flavors)


def test_an_upload_with_author_code_is_refused_before_anything_is_imported(tmp_path):
    """End to end on a real artifact directory: the manifest asks for code on the path, and the
    loader never gets as far as MLflow's."""
    import mlflow.sklearn
    from sklearn.linear_model import LinearRegression
    import numpy as np

    path = str(tmp_path / "m")
    X = np.random.RandomState(0).rand(10, 2)
    mlflow.sklearn.save_model(LinearRegression().fit(X, X[:, 0]), path, serialization_format="skops")
    with open(os.path.join(path, "MLmodel")) as fh:
        manifest = yaml.safe_load(fh)
    manifest["flavors"]["python_function"]["code"] = "code"
    with open(os.path.join(path, "MLmodel"), "w") as fh:
        yaml.safe_dump(manifest, fh)
    with pytest.raises(skops_trust.UnsafeArtifact):
        skops_trust.load_pyfunc(path, "uploaded")
    skops_trust.load_pyfunc(path, "kernel")   # the kernel path is not this rule's to change


# --- the run's model is found however many models the experiment holds -----------------------------

class _Page(list):
    def __init__(self, items, token=None):
        super().__init__(items)
        self.token = token


class _Logged:
    def __init__(self, model_id, run_id):
        self.model_id, self.source_run_id = model_id, run_id


class _Client:
    """An experiment of many logged models, served a page at a time — honouring the filter only if
    one is sent, like the store does."""

    def __init__(self, n=250, page=100):
        self.models = [_Logged(f"m-{i}", f"run{i}") for i in range(n)]
        self.page = page
        self.filters = []

    def get_run(self, run_id):
        return type("R", (), {"info": type("I", (), {"experiment_id": "1"})()})()

    def search_logged_models(self, experiment_ids, filter_string=None, page_token=None, **kw):
        self.filters.append(filter_string)
        models = self.models
        if filter_string:
            wanted = re.search(r"source_run_id = '(\w+)'", filter_string).group(1)
            models = [m for m in models if m.source_run_id == wanted]
        start = int(page_token or 0)
        end = start + self.page
        return _Page(models[start:end], str(end) if end < len(models) else None)


def test_a_run_whose_model_is_past_the_first_page_still_resolves(monkeypatch):
    import mlflow
    client = _Client()
    monkeypatch.setattr(mlflow, "MlflowClient", lambda: client)
    assert model_uri.resolve_model_uri("run240") == "models:/m-240"
    assert client.filters[0] == "source_run_id = 'run240'"


def test_a_run_id_is_never_spliced_into_the_filter_unchecked():
    with pytest.raises(ValueError):
        model_uri.resolve_model_uri("x' OR '1'='1")


# --- one flavor map ---------------------------------------------------------------------------------

def test_the_capabilities_view_uses_the_gates_flavor_map():
    from routers import capabilities

    flavors = {e["flavor"] for e in asyncio.run(capabilities.get_capabilities())["servable_flavors"]
               + asyncio.run(capabilities.get_capabilities())["unservable_flavors"]}
    assert flavors == set(model_compat.FLAVOR_LIBRARY), "the view and the gate disagree on flavors"


# --- created_by is a user ---------------------------------------------------------------------------

def test_a_feature_config_records_the_user_who_created_it():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import auth
    from routers import feature_configs

    seen = {}

    class Repo:
        async def create_feature_config(self, **kw):
            seen.update(kw)
            return {"id": str(uuid.uuid4()), "company_id": kw["company_id"], "equipment_type": "p",
                    "name": "n", "description": None, "base_sensors": [], "transformations": [],
                    "created_at": "2026-10-13T00:00:00Z", "updated_at": "2026-10-13T00:00:00Z",
                    "created_by": kw["created_by"]}

    cid = str(uuid.uuid4())
    app = FastAPI()
    app.include_router(feature_configs.router)
    app.state.ml_repository = Repo()
    app.dependency_overrides[auth.verify_jwt_token] = lambda: {"user_id": "user-42", "payload": {}}
    app.dependency_overrides[feature_configs.get_company_id_dependency] = lambda: cid
    r = TestClient(app).post("/api/feature-configs", json={
        "equipment_type": "p", "name": "n", "base_sensors": [], "transformations": []})
    assert r.status_code == 201, r.text
    assert seen["created_by"] == "user-42"


# --- what the image carries -------------------------------------------------------------------------

def test_no_tracking_database_sits_in_the_image_source():
    """`COPY api/ .` ships whatever is in api/. A committed MLflow SQLite store shipped with it."""
    stray = [p for p in glob.glob(os.path.join(API, "**", "*"), recursive=True)
             if re.search(r"\.(db|sqlite)$|/mlruns(/|$)", p)]
    assert not stray, f"{stray} would be copied into the serving image"


def test_the_build_context_excludes_what_the_image_must_not_carry():
    path = os.path.join(API, "..", ".dockerignore")
    assert os.path.exists(path), "services/ml_service has no .dockerignore"
    with open(path) as fh:
        rules = {line.strip() for line in fh if line.strip() and not line.startswith("#")}
    for needed in ("tests/", "requirements-dev.txt", "**/*.db", "**/mlruns/"):
        assert needed in rules, f".dockerignore does not exclude {needed}"
    assert not any(r in ("api/", "api", "**/*.py", "*.py") for r in rules), "it excludes the service itself"


# The round-trip workflow's path filter is guarded by tests/config_guards/test_ci_path_filters.py,
# which follows the harness's imports transitively and matches them with the filter's glob semantics.


# --- one bucket, named in two deployments -----------------------------------------------------------

def test_compose_tells_ml_service_the_bucket_the_gateway_admits_into():
    with open(os.path.join(ROOT, "docker-compose.yml")) as fh:
        services = yaml.safe_load(fh)["services"]
    ml = services["ml-service-api"]["environment"]["UPLOAD_ARTIFACT_BUCKET"]
    gateway = services["notebook-tracking-gateway"]["environment"]["TRACKING_ARTIFACT_BUCKET"]
    assert ml == gateway


def test_the_chart_tells_ml_service_the_gateways_bucket():
    with open(os.path.join(ROOT, "deploy", "helm", "industryflow", "templates", "configmap.yaml")) as fh:
        body = fh.read()
    assert re.search(r"UPLOAD_ARTIFACT_BUCKET: \{\{ \.Values\.notebookHub\.trackingGateway\.artifactBucket", body)
