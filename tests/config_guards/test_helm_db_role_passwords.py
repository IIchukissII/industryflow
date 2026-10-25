# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every role the schema scripts create must receive its password, in every chart toggle state.

The role scripts read `${<ROLE>_DB_PASSWORD}` from the environment of the migration runner (the
db-migrate Job, ADR-0034). A key that sits in its Secret but is not passed into the container
(cold_export_user, before this guard) creates the role with an empty password, and nothing fails
until the job that logs in as it does. The database server itself holds none of them.
"""

from __future__ import annotations

import re

import pytest
from _repo import ROOT, find, helm_template

TOGGLES = {
    "defaults": [],
    "notebooks": ["--set", "notebookHub.enabled=true"],
    "no-cold-export": ["--set", "coldExport.enabled=false"],
    "notebooks-no-cold-export": ["--set", "notebookHub.enabled=true", "--set", "coldExport.enabled=false"],
    "no-tenant-delete": ["--set", "tenantDelete.enabled=false"],
}


def _passwords(names) -> set[str]:
    return {n for n in names if n.endswith("_DB_PASSWORD")}


def _secret_and_env(args):
    docs = helm_template(*args)
    secret = find(docs, "Secret", "-db-migrate-secrets")
    job = next(d for d in docs if d["kind"] == "Job" and "-db-migrate-" in d["metadata"]["name"])
    container = next(c for c in job["spec"]["template"]["spec"]["containers"] if c["name"] == "db-migrate")
    env = {e["name"]: e for e in container.get("env", [])}
    return secret, env


@pytest.mark.parametrize("toggle", TOGGLES)
def test_the_secret_and_the_container_carry_the_same_role_passwords(toggle):
    secret, env = _secret_and_env(TOGGLES[toggle])
    in_secret = _passwords(secret["stringData"])
    in_env = _passwords(env)
    assert in_secret == in_env, (
        f"[{toggle}] in the Secret but not passed to the DB: {sorted(in_secret - in_env)}; "
        f"passed but not in the Secret: {sorted(in_env - in_secret)}"
    )
    for name in in_env:
        ref = env[name]["valueFrom"]["secretKeyRef"]
        assert ref["key"] == name and ref["name"] == secret["metadata"]["name"]


def test_every_password_an_init_script_reads_reaches_the_container():
    consumed = set()
    for script in (ROOT / "infrastructure" / "timescaledb" / "init-scripts").iterdir():
        consumed |= set(re.findall(r"\$\{?([A-Z_]+_DB_PASSWORD)", script.read_text()))
    assert consumed, "no init script reads a role password — the guard is not looking"
    # With every optional plane on, every role an init script can create must be served.
    _, env = _secret_and_env(TOGGLES["notebooks"])
    missing = consumed - _passwords(env)
    assert not missing, f"init scripts read passwords the chart never passes: {sorted(missing)}"


@pytest.mark.parametrize("toggle", TOGGLES)
def test_the_database_server_holds_no_role_password(toggle):
    docs = helm_template(*TOGGLES[toggle])
    secret = find(docs, "Secret", "-timescaledb-secrets")
    sts = find(docs, "StatefulSet", "-timescaledb")
    env = {e["name"] for c in sts["spec"]["template"]["spec"]["containers"] for e in c.get("env", [])}
    assert not _passwords(secret["stringData"]) | _passwords(env), f"[{toggle}] role passwords on the DB server"
