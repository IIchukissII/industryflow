# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Lists a new job is wired into by hand, each of whose omissions fails only when the job runs.

* Every login role an init script creates must have a pg_hba.conf entry: a role without one is refused
  at its first connection, the job and its schema-ready gate alike.
* The roles a schema-ready gate logs in as (ADR-0034 dec 10) must read the ledger, granted by
  28-schema-ledger-read.sql; a role left out waits for a ledger it cannot read, and its job never starts.
* An image started as ``python -m <package>.main`` must ship that package: a Dockerfile copied from
  another job kept the other's entrypoint, and the new job would only ever fail to import it.
"""
from __future__ import annotations

import re

from _repo import ROOT, load_yaml

LEDGER = (ROOT / "infrastructure/timescaledb/init-scripts/28-schema-ledger-read.sql").read_text()


def _ledger_readers() -> set[str]:
    block = LEDGER[LEDGER.index("ARRAY["):LEDGER.index("] LOOP")]
    return set(re.findall(r"'(\w+)'", block))


def _gate_roles() -> set[str]:
    roles = set()
    for name, service in load_yaml("docker-compose.yml")["services"].items():
        if name.endswith("-schema-ready"):
            user = str((service.get("environment") or {}).get("PGUSER", ""))
            if user and "$" not in user:
                roles.add(user)
    for template in (ROOT / "deploy/helm/industryflow/templates").glob("*.yaml"):
        roles.update(re.findall(r'schemaReadyInitContainer" \(dict "ctx" \. "user" "(\w+)"', template.read_text()))
    return roles


def test_every_role_a_schema_ready_gate_logs_in_as_reads_the_ledger():
    gates = _gate_roles()
    assert {"cold_export_user", "tenant_delete_user"} <= gates, f"the guard reads no gates: {sorted(gates)}"
    assert not gates - _ledger_readers(), f"gates whose role cannot read the ledger: {sorted(gates - _ledger_readers())}"


def test_every_module_entrypoint_names_the_package_its_image_ships():
    checked = []
    for dockerfile in sorted(ROOT.glob("services/*/Dockerfile")):
        text = dockerfile.read_text()
        m = re.search(r'ENTRYPOINT \["python", "-m", "(\w+)\.main"\]', text)
        if not m:
            continue
        checked.append(dockerfile.parent.name)
        assert re.search(rf"COPY \. \./{m.group(1)}/", text), \
            f"{dockerfile.relative_to(ROOT)} runs {m.group(1)}.main but ships no {m.group(1)} package"
    assert {"cold_export", "tenant_usage"} <= set(checked), checked


HBA = (ROOT / "infrastructure/timescaledb/config/pg_hba.conf").read_text()


def test_every_login_role_has_a_pg_hba_entry():
    created = set(re.findall(r"CREATE ROLE (\w+) WITH LOGIN", (ROOT / "infrastructure/timescaledb/init-scripts/00-create-roles.sh").read_text()))
    assert {"api_gateway_user", "cold_export_user"} <= created, f"the guard reads no roles: {sorted(created)}"
    entries = set()
    for line in HBA.splitlines():
        fields = line.split()
        if len(fields) >= 3 and fields[0].startswith("host"):
            entries.update(fields[2].split(","))
    missing = sorted(created - entries)
    assert not missing, f"login roles pg_hba.conf never admits: {missing}"
