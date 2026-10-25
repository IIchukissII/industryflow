# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The platform schema sequence and the clients that depend on it (ADR-0034 dec 1, 4, 7-10).

Three lists whose omission fails silently:

  * a script without a kind would be refused by the runner on deploy, not in CI; a migration that
    defines a function could never change that function again (a migration is never edited);
  * a Compose database client that does not wait for db-migrate starts against a database the
    ledger shows behind, and on a fresh volume against no schema at all;
  * a chart workload that holds a database password but has no schema-ready init container does
    the same on Kubernetes, which has no start order across workloads.
"""

from __future__ import annotations

import re

import pytest
from _repo import ROOT, helm_template, load_yaml

SCRIPTS = ROOT / "infrastructure" / "timescaledb" / "init-scripts"
COMPOSE = load_yaml("docker-compose.yml")["services"]
KIND = re.compile(r"^(?:--|#) *migration-kind: *(\w+) *$", re.M)

# Not gated, each for a stated reason: the server; the runner itself; the backup, which must work
# while the ledger is behind, because it is the step taken before an upgrade (ADR-0034 dec 5).
COMPOSE_UNGATED = {"timescaledb", "db-migrate", "db-backup"}
CHART_UNGATED = ("-timescaledb", "-db-migrate-", "-db-backup")


def _scripts():
    found = sorted(p for p in SCRIPTS.iterdir() if re.match(r"\d", p.name))
    assert found, "no numbered scripts — the guard is not looking"
    return found


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_every_script_declares_its_kind(script):
    kinds = KIND.findall("\n".join(script.read_text().splitlines()[:40]))
    assert kinds in (["migration"], ["definition"]), (
        f"{script.name}: declare exactly one `migration-kind: migration|definition` header"
    )


@pytest.mark.parametrize("script", _scripts(), ids=lambda p: p.name)
def test_a_migration_defines_no_function(script):
    text = script.read_text()
    if KIND.search(text).group(1) == "migration":
        assert not re.search(r"CREATE\s+(OR\s+REPLACE\s+)?FUNCTION", text, re.I), (
            f"{script.name} is a migration but defines a function; it could never change it again"
        )


def _uses_database(svc: dict) -> bool:
    env = svc.get("environment") or {}
    text = " ".join(f"{k}={v}" for k, v in env.items()) if isinstance(env, dict) else " ".join(env)
    return bool(re.search(r"DB_PASSWORD|PGPASSWORD|PGHOST|DB_HOST|\btimescaledb\b", text))


def test_the_guard_sees_the_database_clients():
    clients = {n for n, s in COMPOSE.items() if _uses_database(s)}
    assert {"api-gateway", "tenant-delete", "notebook-sql-proxy"} <= clients, clients


@pytest.mark.parametrize("name", sorted(n for n, s in COMPOSE.items() if _uses_database(s)))
def test_every_compose_database_client_waits_for_the_runner(name):
    if name in COMPOSE_UNGATED:
        pytest.skip("ungated by design")
    deps = COMPOSE[name].get("depends_on") or {}
    assert isinstance(deps, dict) and deps.get("db-migrate", {}).get("condition") == (
        "service_completed_successfully"
    ), f"{name} uses the database but does not wait for db-migrate to complete"


def test_the_runner_is_the_only_applier_on_compose():
    mounts = COMPOSE["timescaledb"].get("volumes", [])
    assert not [m for m in mounts if "docker-entrypoint-initdb.d" in m], (
        "the database runs the scripts under its init hook; db-migrate is the only applier"
    )
    assert "apply" in COMPOSE["db-migrate"]["command"]


TOGGLES = {
    "defaults": [],
    "everything": ["--set", "notebookHub.enabled=true", "--set", "tenantDelete.enabled=true",
                   "--set", "coldExport.enabled=true"],
    "external-db": ["--set", "infra.timescaledb.inCluster=false",
                    "--set", "infra.timescaledb.external.host=db.example"],
    "no-db-tls": ["--set", "tls.db.enabled=false", "--set", "notebookHub.enabled=true",
                  "--set", "tenantDelete.enabled=true"],
}


def _pods(docs):
    for d in docs:
        spec = d.get("spec") or {}
        tmpl = spec.get("jobTemplate", {}).get("spec", {}).get("template") or spec.get("template")
        if tmpl and "spec" in tmpl:
            yield d, tmpl["spec"]


@pytest.mark.parametrize("toggle", TOGGLES)
def test_every_chart_database_client_waits_for_a_current_ledger(toggle):
    docs = helm_template(*TOGGLES[toggle])
    secrets = {d["metadata"]["name"]: d.get("stringData", {}) for d in docs if d["kind"] == "Secret"}
    missing, gated = [], 0
    for doc, pod in _pods(docs):
        name = doc["metadata"]["name"]
        direct = any(
            (e.get("valueFrom") or {}).get("secretKeyRef", {}).get("key", "").endswith("DB_PASSWORD")
            for c in pod.get("containers", []) for e in c.get("env", [])
        )
        via_env_from = any(
            k.endswith("_DB_PASSWORD")
            for c in pod.get("containers", []) for ref in c.get("envFrom", []) if "secretRef" in ref
            for k in secrets.get(ref["secretRef"]["name"], {})
        )
        if not (direct or via_env_from) or any(s in name for s in CHART_UNGATED):
            continue
        if any(c["name"] == "schema-ready" for c in pod.get("initContainers", [])):
            gated += 1
        else:
            missing.append(f"{doc['kind']}/{name}")
    assert not missing, f"[{toggle}] database clients with no schema-ready init container: {missing}"
    assert gated >= 5, f"[{toggle}] only {gated} gated workloads — the guard is not looking"


@pytest.mark.parametrize("toggle", TOGGLES)
def test_the_chart_runs_the_sequence_on_every_revision(toggle):
    docs = helm_template(*TOGGLES[toggle])
    jobs = [d for d in docs if d["kind"] == "Job" and "-db-migrate-" in d["metadata"]["name"]]
    assert len(jobs) == 1, f"[{toggle}] expected one db-migrate Job, found {len(jobs)}"
    assert jobs[0]["spec"]["template"]["spec"]["containers"][0]["command"][-1] == "apply"
    for _, pod in _pods(docs):
        for c in pod.get("containers", []):
            assert not [m for m in c.get("volumeMounts", []) if m["mountPath"] == "/docker-entrypoint-initdb.d"]
