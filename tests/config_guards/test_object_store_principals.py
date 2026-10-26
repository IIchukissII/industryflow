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

import json
import os
import re
import subprocess

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
    "tenant-delete": ("TENANT_DELETE_COLD", "TENANT_DELETE_COLD_ACCESS_KEY", "TENANT_DELETE_COLD_SECRET_KEY"),
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


# The policy each principal is created with, rendered by the script's own policy functions (so what
# is asserted is what `mc admin policy create` receives, not a reading of the call's argument names).
_BUCKETS = {"SPARK_CHECKPOINT_BUCKET": "cp", "ARTIFACT_BUCKET": "art", "BACKUP_BUCKET": "bak", "COLD_STORE_BUCKET": "cold"}
_WRITE_VERBS = {"s3:PutObject", "s3:DeleteObject", "s3:AbortMultipartUpload"}


def _granted() -> dict[str, dict[str, set[str]]]:
    """principal -> bucket -> the actions its policy allows on that bucket's objects."""
    start = SCRIPT.index("rw_policy() {")
    functions = SCRIPT[start:SCRIPT.index("\n# principal <name>", start)]
    calls = re.findall(r"^principal (\S+) \w+ \w+ \S+ (\w+_policy .*)$", SCRIPT, re.M)
    assert len(calls) == len(_principals()), "a principal line the guard cannot render"
    out = {}
    for name, call in calls:
        r = subprocess.run(["sh", "-euc", functions + "\n" + call], capture_output=True, text=True,
                           env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **_BUCKETS}, check=True)
        grants: dict[str, set[str]] = {}
        for st in json.loads(r.stdout)["Statement"]:
            assert st["Effect"] == "Allow", st
            for res in st["Resource"]:
                bucket, _, key = res.removeprefix("arn:aws:s3:::").partition("/")
                if key:
                    grants.setdefault(bucket, set()).update(st["Action"])
        out[name] = grants
    return out


def test_the_tracking_gateway_is_the_only_principal_that_writes_the_artifact_store():
    # ADR-0030 dec 2 / ADR-0019: one privileged writer to the store every tenant's models live in.
    # The MLflow server only serves reads; its artifact proxy is unauthenticated, so a write verb on
    # its principal is a write path open to anything that can reach the server.
    writers = {n for n, g in _granted().items() if g.get("art", set()) & _WRITE_VERBS}
    assert writers == {"tracking-gateway"}, f"principals that can write or delete artifacts: {sorted(writers)}"


def test_each_principal_holds_only_the_verbs_it_uses():
    granted = _granted()
    writes = {n for n, g in granted.items() if any(v & _WRITE_VERBS for v in g.values())}
    deletes = {n for n, g in granted.items() if any("s3:DeleteObject" in v for v in g.values())}
    # (Deleting counts as writing here; tenant-delete-cold may only delete, checked below.)
    assert writes == {"spark-checkpoint", "tracking-gateway", "db-backup", "cold-export-rw",
                      "tenant-delete-cold"}, sorted(writes)
    # The cold exporter never removes history it archived (ADR-0025 dec 11); only the tenant-delete
    # job removes cold objects (dec 14).
    assert deletes == {"spark-checkpoint", "tracking-gateway", "db-backup", "tenant-delete-cold"}, sorted(deletes)
    assert granted["mlflow-artifacts"] == {"art": {"s3:GetObject"}}, granted["mlflow-artifacts"]


def test_only_the_tenant_delete_job_deletes_in_the_cold_bucket_and_it_sees_nothing():
    # ADR-0025 dec 14: no principal both writes and deletes the only copy of history, and the
    # deleter can neither read nor write an object.
    granted = _granted()
    cold_deleters = {n for n, g in granted.items() if "s3:DeleteObject" in g.get("cold", set())}
    assert cold_deleters == {"tenant-delete-cold"}, sorted(cold_deleters)
    assert granted["tenant-delete-cold"] == {"cold": {"s3:DeleteObject"}}, granted["tenant-delete-cold"]


def test_the_backup_mirrors_read_with_the_backup_read_principal():
    # ADR-0038 dec 3: the mirrors' source keys are the backup job's own read principal, in compose and
    # in the chart — not the cold broker's, the MLflow server's or serving's.
    env = COMPOSE["db-backup"]["environment"]
    assert _interpolated(env["SOURCE_ACCESS_KEY_ID"]) == {"BACKUP_READ_ACCESS_KEY"}
    assert _interpolated(env["SOURCE_SECRET_ACCESS_KEY"]) == {"BACKUP_READ_SECRET_KEY"}
    docs = helm_template(*ALL_ON)
    c = find(docs, "CronJob", "-db-backup")["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]
    refs = {e["name"]: e["valueFrom"]["secretKeyRef"]["key"]
            for e in c["env"] if "secretKeyRef" in e.get("valueFrom", {})}
    assert refs["SOURCE_ACCESS_KEY_ID"] == "BACKUP_READ_ACCESS_KEY"
    assert refs["AWS_ACCESS_KEY_ID"] == "BACKUP_STORE_ACCESS_KEY"
    assert not {"COLD_READ_ACCESS_KEY", "MLFLOW_STORE_ACCESS_KEY", "MODEL_READ_ACCESS_KEY"} & set(refs.values())


def test_only_the_backup_job_holds_the_backup_read_principal():
    holders = {n for n, s in COMPOSE.items()
               if _interpolated(s.get("environment", {})) & {"BACKUP_READ_ACCESS_KEY", "BACKUP_READ_SECRET_KEY"}}
    assert holders == {"db-backup", "minio-init"}, sorted(holders)
    assert _granted()["backup-read"] == {"art": {"s3:GetObject"}, "cold": {"s3:GetObject"}}


def test_the_notebook_backup_holds_no_database_or_source_credential():
    # ADR-0038 dec 5: the Docker socket and the backup bucket's principal, nothing else.
    held = _interpolated(COMPOSE["notebook-backup"]["environment"])
    assert held & {"BACKUP_STORE_ACCESS_KEY", "BACKUP_STORE_SECRET_KEY"} == {"BACKUP_STORE_ACCESS_KEY", "BACKUP_STORE_SECRET_KEY"}
    assert not {v for v in held if v.endswith(("_PASSWORD", "_ACCESS_KEY", "_SECRET_KEY"))
                and not v.startswith("BACKUP_STORE_")}, sorted(held)
