# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""No process is started with a secret on its command line, in either deployment.

A command line is readable by every process on the host (`ps`, /proc/<pid>/cmdline), lands in
container inspection output and in crash reports; the environment is not. The MLflow server took its
database password inside --backend-store-uri in both deployments. This reads every compose service's
command, entrypoint and healthcheck and every rendered container's command and args, and fails on a
reference to a secret: a variable whose name says so, or (in the chart) one sourced from a Secret.
"""

from __future__ import annotations

import re

from _repo import helm_template, load_yaml

# Names that hold a secret. An access key id is an identifier, not a secret.
SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|PRIVATE|_PASS\b|_PASS_|(?<!ACCESS)_KEY\b")
# ${VAR} / $VAR as compose and a shell read them, $(VAR) as Kubernetes expands it.
REFERENCE = re.compile(r"\$\{(\w+)|\$\((\w+)\)|\$\$?([A-Za-z_]\w*)")
ALL_ON = ["--set", "notebookHub.enabled=true", "--set", "backup.enabled=true", "--set", "coldExport.enabled=true",
          "--set", "tenantDelete.enabled=true"]


def _references(argv) -> set[str]:
    if argv is None:
        return set()
    text = " ".join(argv) if isinstance(argv, list) else str(argv)
    return {next(g for g in m if g) for m in REFERENCE.findall(text)}


def test_the_guard_sees_a_secret_reference():
    assert _references(["mlflow", "--uri", "postgresql://u:${MLFLOW_DB_PASSWORD}@h/db"]) == {"MLFLOW_DB_PASSWORD"}
    assert _references(["--uri", "postgresql://$(U):$(MLFLOW_DB_PASSWORD)@h"]) == {"U", "MLFLOW_DB_PASSWORD"}
    assert SECRET_NAME.search("MLFLOW_DB_PASSWORD") and SECRET_NAME.search("TRACKING_S3_SECRET_KEY")
    assert not SECRET_NAME.search("COLD_STORE_ACCESS_KEY")


def test_no_compose_command_line_carries_a_secret():
    services = load_yaml("docker-compose.yml")["services"]
    assert len(services) >= 20, "the guard is not reading the compose file"
    found = []
    for name, svc in services.items():
        lines = [svc.get("command"), svc.get("entrypoint"), (svc.get("healthcheck") or {}).get("test")]
        for var in set().union(*map(_references, lines)):
            if SECRET_NAME.search(var):
                found.append(f"{name}: {var}")
    assert not found, "secrets on a command line (pass them in the environment):\n" + "\n".join(sorted(found))


def _containers(docs):
    for d in docs:
        spec = d.get("spec", {})
        tmpl = spec.get("template") or spec.get("jobTemplate", {}).get("spec", {}).get("template")
        if tmpl:
            pod = tmpl["spec"]
            for c in pod.get("initContainers", []) + pod.get("containers", []):
                yield f"{d['metadata']['name']}/{c['name']}", c


def test_no_chart_command_line_carries_a_secret():
    docs = helm_template(*ALL_ON)
    secret_keys = {k for d in docs if d.get("kind") == "Secret" for k in {**d.get("stringData", {}), **d.get("data", {})}}
    found, seen = [], 0
    for name, c in _containers(docs):
        seen += 1
        from_secret = {e["name"] for e in c.get("env", []) if "secretKeyRef" in e.get("valueFrom", {})}
        if any("secretRef" in src for src in c.get("envFrom", [])):
            from_secret |= secret_keys
        for var in _references(c.get("command")) | _references(c.get("args")):
            if SECRET_NAME.search(var) or var in from_secret:
                found.append(f"{name}: {var}")
    assert seen >= 20, "the guard is not reading the rendered chart"
    assert not found, "secrets on a command line (pass them in the environment):\n" + "\n".join(sorted(found))
