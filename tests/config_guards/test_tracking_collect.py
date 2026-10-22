# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The tracking server's collection and the gateway agree on where a deleted tenant's experiments go.

The collection hard-deletes deleted experiments under one name root (ADR-0033 dec 4); the gateway
renames a deleted tenant's experiments there from the shared contract. The collection runs in the
tracking server's image, which does not carry the contract, so it keeps its own copy: a copy that
drifts makes the collection miss every deleted tenant, or take entries it must not.
"""

from __future__ import annotations

import ast

from _repo import ROOT, helm_template, load_yaml
from industryflow_contracts import tenancy

COLLECTOR = ROOT / "infrastructure" / "mlflow" / "collect_deleted.py"


def _constant(name: str) -> str:
    for node in ast.parse(COLLECTOR.read_text()).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{COLLECTOR.name} defines no {name}")


def test_the_collection_reads_the_root_the_gateway_writes():
    assert _constant("DELETED_NAME_ROOT") == tenancy.MLFLOW_DELETED_NAME_ROOT
    assert tenancy.mlflow_deleted_name_prefix("550e8400-e29b-41d4-a716-446655440000").startswith(
        tenancy.MLFLOW_DELETED_NAME_ROOT + "tenant_")


def test_the_mlflow_image_carries_the_collection():
    assert "COPY collect_deleted.py /opt/industryflow/collect_deleted.py" in (COLLECTOR.parent / "Dockerfile").read_text()


def test_compose_and_chart_run_the_collection_with_the_servers_own_credential():
    svc = load_yaml("docker-compose.yml")["services"]["tracking-collect"]
    assert svc["command"] == ["python", "/opt/industryflow/collect_deleted.py"]
    assert svc["environment"]["PGPASSWORD"] == "${MLFLOW_DB_PASSWORD}"
    docs = helm_template("--set", "notebookHub.enabled=true")
    [job] = [d for d in docs if d.get("kind") == "CronJob" and d["metadata"]["name"].endswith("-tracking-collect")]
    container = job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    assert container["command"] == ["python", "/opt/industryflow/collect_deleted.py"]
    env = {e["name"]: e for e in container["env"]}
    assert env["PGPASSWORD"]["valueFrom"]["secretKeyRef"]["key"] == "MLFLOW_DB_PASSWORD"
    assert env["MLFLOW_TRACKING_URI"]["value"].startswith("http")


def test_the_delete_job_and_the_gateway_share_the_operator_secret_in_both_deployments():
    compose = load_yaml("docker-compose.yml")["services"]
    assert compose["notebook-tracking-gateway"]["environment"]["TRACKING_OPERATOR_TOKEN"] == "${TRACKING_OPERATOR_TOKEN:-}"
    assert compose["tenant-delete"]["environment"]["TENANT_DELETE_TRACKING_TOKEN"] == "${TRACKING_OPERATOR_TOKEN:-}"
    docs = helm_template("--set", "notebookHub.enabled=true")

    def env_of(kind, suffix):
        [d] = [d for d in docs if d.get("kind") == kind and d["metadata"]["name"].endswith(suffix)]
        spec = d["spec"]["jobTemplate"]["spec"] if kind == "CronJob" else d["spec"]
        return {e["name"]: e for e in spec["template"]["spec"]["containers"][0]["env"]}

    gateway = env_of("Deployment", "-notebook-tracking-gateway")["TRACKING_OPERATOR_TOKEN"]
    job = env_of("CronJob", "-tenant-delete")
    assert gateway["valueFrom"]["secretKeyRef"]["key"] == job["TENANT_DELETE_TRACKING_TOKEN"]["valueFrom"]["secretKeyRef"]["key"]
    assert job["TENANT_DELETE_TRACKING_URL"]["value"].endswith("-notebook-tracking-gateway:5050")
