# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every role the database creates at init must receive its password, in every chart toggle state.

The init scripts read `${<ROLE>_DB_PASSWORD}` from the container environment. A key that sits in the
timescaledb Secret but is not passed into the container (cold_export_user, before this guard) creates
the role with an empty password, and nothing fails until the job that logs in as it does.
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
    secret = find(docs, "Secret", "-timescaledb-secrets")
    sts = find(docs, "StatefulSet", "-timescaledb")
    container = next(c for c in sts["spec"]["template"]["spec"]["containers"] if c["name"] == "timescaledb")
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
