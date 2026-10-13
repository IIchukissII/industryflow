# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Who holds which object-store credential, in both deployments.

Every service used to authenticate with the MinIO root key: the Spark checkpoint writer could read
every tenant's models and backups (ADR-0025 dec 11 keeps it checkpoint-only), and serving held a key
to the whole store (ADR-0030 dec 2). Now minio-init.sh creates one scoped principal per consumer;
these guards read the script's principal list and both deployments' wiring, and fail if the root key
reaches a service or a principal is created without being supplied (or supplied without being created).
The live enforcement of each policy is tested against a real MinIO in tests/object_store.
"""

from __future__ import annotations

import re

from _repo import ROOT, find, helm_template, load_yaml

SCRIPT = (ROOT / "infrastructure" / "minio" / "minio-init.sh").read_text()
COMPOSE = load_yaml("docker-compose.yml")["services"]
ROOT_KEYS = {"MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"}
ALL_ON = ["--set", "notebookHub.enabled=true", "--set", "backup.enabled=true", "--set", "coldExport.enabled=true"]


def _principals() -> dict[str, tuple[str, str]]:
    found = dict((m[0], (m[1], m[2])) for m in re.findall(r"^principal (\S+) (\w+) (\w+) ", SCRIPT, re.M))
    assert len(found) >= 7, f"found {sorted(found)} — the guard is not reading the script"
    return found


def _interpolated(value) -> set[str]:
    return set(re.findall(r"\$\{(\w+)", str(value)))


# Which principal each consumer authenticates with, and the env names it reads it under.
COMPOSE_CONSUMERS = {
    "spark-streaming": ("SPARK_CHECKPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"),
    "spark-aggregations": ("SPARK_CHECKPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"),
    "mlflow": ("MLFLOW_STORE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    "ml-service-api": ("MODEL_READ", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    "notebook-tracking-gateway": ("TRACKING_STORE", "TRACKING_S3_ACCESS_KEY", "TRACKING_S3_SECRET_KEY"),
    "db-backup": ("BACKUP_STORE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"),
    "cold-export": ("COLD_STORE", "COLD_STORE_ACCESS_KEY", "COLD_STORE_SECRET_KEY"),
    "notebook-cold-broker": ("COLD_READ", "COLD_READ_ACCESS_KEY", "COLD_READ_SECRET_KEY"),
}


def test_compose_root_key_reaches_only_the_server_and_the_bootstrap():
    holders = {n for n, s in COMPOSE.items() if _interpolated(s.get("environment", {})) & ROOT_KEYS}
    assert holders == {"minio", "minio-init"}, f"the root object-store key reaches {sorted(holders - {'minio', 'minio-init'})}"


def test_compose_consumers_hold_their_own_principal():
    principals = {access.removesuffix("_ACCESS_KEY") for access, _ in _principals().values()}
    wrong = []
    for service, (prefix, access_env, secret_env) in COMPOSE_CONSUMERS.items():
        assert prefix in principals, f"{prefix} is not a principal the script creates"
        env = COMPOSE[service]["environment"]
        if _interpolated(env.get(access_env)) != {f"{prefix}_ACCESS_KEY"} \
                or _interpolated(env.get(secret_env)) != {f"{prefix}_SECRET_KEY"}:
            wrong.append(f"{service}: {access_env}={env.get(access_env)} {secret_env}={env.get(secret_env)}")
    assert not wrong, "consumers not on their own principal:\n" + "\n".join(wrong)


def test_every_principal_is_supplied_to_the_compose_bootstrap_and_templated():
    env = COMPOSE["minio-init"]["environment"]
    example = set(re.findall(r"^([A-Z0-9_]+)=", (ROOT / ".env.example").read_text(), re.M))
    missing = []
    for name, (access, secret) in _principals().items():
        for var in (access, secret):
            if _interpolated(env.get(var)) != {var}:
                missing.append(f"minio-init env {var}")
            if var not in example:
                missing.append(f".env.example {var}")
    assert not missing, f"principals not wired: {missing}"


def _pods(docs):
    for d in docs:
        spec = d.get("spec", {})
        tmpl = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get("template")
        if tmpl:
            yield d["metadata"]["name"], tmpl["spec"]


def test_chart_root_key_reaches_only_the_server_and_the_bootstrap():
    docs = helm_template(*ALL_ON)
    holders = set()
    for name, pod in _pods(docs):
        for c in pod.get("containers", []) + pod.get("initContainers", []):
            keys = {e["valueFrom"]["secretKeyRef"]["key"] for e in c.get("env", [])
                    if "secretKeyRef" in e.get("valueFrom", {})}
            if keys & ROOT_KEYS:
                holders.add(name)
            for src in c.get("envFrom", []):
                ref = src.get("secretRef", {}).get("name")
                secret = next((s for s in docs if s["kind"] == "Secret" and s["metadata"]["name"] == ref), None)
                if secret and set(secret.get("stringData", {})) & ROOT_KEYS:
                    holders.add(name)
    allowed = {"if-industryflow-minio", "if-industryflow-minio-init"}
    assert holders <= allowed and "if-industryflow-minio" in holders, f"root key holders: {sorted(holders)}"


def test_chart_bootstrap_receives_every_principal_its_consumers_use():
    docs = helm_template(*ALL_ON)
    job = find(docs, "Job", "-minio-init")
    env = {e["name"] for e in job["spec"]["template"]["spec"]["containers"][0]["env"]}
    data = load_yaml("deploy/helm/industryflow/values.yaml")["secrets"]["data"]
    missing = [v for pair in _principals().values() for v in pair if v not in env or v not in data]
    assert not missing, f"principals the Job or secrets.data lacks: {missing}"


def test_chart_mlflow_server_uses_the_database_and_the_object_store():
    docs = helm_template()
    args = find(docs, "Deployment", "-mlflow")["spec"]["template"]["spec"]["containers"][0].get("args", [])
    joined = " ".join(args)
    assert "--backend-store-uri postgresql://" in joined, args
    assert "--artifacts-destination s3://" in joined and "--default-artifact-root mlflow-artifacts:/" in joined, args


def test_the_backup_cold_mirror_reads_with_the_cold_read_principal():
    # The backup principal cannot read the cold bucket; the mirror's source keys must be the cold
    # read principal's, in compose and in the chart.
    env = COMPOSE["db-backup"]["environment"]
    assert _interpolated(env["COLD_SOURCE_ACCESS_KEY_ID"]) == {"COLD_READ_ACCESS_KEY"}
    assert _interpolated(env["COLD_SOURCE_SECRET_ACCESS_KEY"]) == {"COLD_READ_SECRET_KEY"}
    docs = helm_template(*ALL_ON)
    c = find(docs, "CronJob", "-db-backup")["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    refs = {e["name"]: e["valueFrom"]["secretKeyRef"]["key"]
            for e in c["env"] if "secretKeyRef" in e.get("valueFrom", {})}
    assert refs["COLD_SOURCE_ACCESS_KEY_ID"] == "COLD_READ_ACCESS_KEY"
    assert refs["AWS_ACCESS_KEY_ID"] == "BACKUP_STORE_ACCESS_KEY"
