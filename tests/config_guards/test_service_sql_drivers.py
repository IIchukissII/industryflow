# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The DB proofs run a service's SQL on the driver that service uses (infrastructure/timescaledb/tests/
service_sql.py). Three lists must agree, and each fails silently when it does not:

  * service_sql.DRIVERS against the services' code: a service that changes driver, or a service whose
    SQL a proof reads without a recorded driver, would be proved on the wrong one;
  * the proofs against service_sql: a proof that reads a service's source with its own loader chooses
    its own driver again, which is how a psycopg2 proof passed while asyncpg refused the write (#502);
  * the proofs' pins against the services' pins: a driver one version off is not the service's driver.
"""
import re
import sys

from _repo import ROOT

TESTS = ROOT / "infrastructure" / "timescaledb" / "tests"
sys.path.insert(0, str(TESTS))

import service_sql  # noqa: E402

# How each driver is opened in a service's own code.
OPENERS = {
    service_sql.SQLALCHEMY_ASYNCPG: re.compile(r"\bcreate_async_engine\("),
    service_sql.ASYNCPG: re.compile(r"\basyncpg\.(?:create_pool|connect)\("),
    service_sql.PSYCOPG2: re.compile(r"\bpsycopg2\.connect\("),
}


def _service_code(prefix):
    for path in sorted((ROOT / prefix).rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/tests/" not in rel:
            yield rel, path.read_text()


def _drivers_used(prefix):
    return {driver for _, src in _service_code(prefix) for driver, opener in OPENERS.items() if opener.search(src)}


def test_each_recorded_driver_is_the_one_the_service_opens():
    for prefix, driver in service_sql.DRIVERS.items():
        assert _drivers_used(prefix) == {driver}, f"{prefix} opens {_drivers_used(prefix)}, DRIVERS says {driver}"


def _proofs():
    for path in sorted(TESTS.glob("test_*.py")):
        yield path.name, path.read_text()


def test_every_service_source_a_proof_reads_has_a_recorded_driver():
    for name, src in _proofs():
        for service in set(re.findall(r'"(services/[a-z_]+/)', src)):
            assert service in service_sql.DRIVERS, f"{name} reads {service} SQL, which has no recorded driver"


def test_no_proof_loads_service_sql_with_a_loader_of_its_own():
    for name, src in _proofs():
        if '"services/' in src:
            assert "ast.parse" not in src and "ast.literal_eval" not in src, (
                f"{name} reads service source itself; read it with service_sql.statement / literal")


def _pins(path):
    pins = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]*\])?==(\S+)", line.strip())
        if m:
            pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    return pins


def test_the_proofs_pin_what_the_services_run():
    proofs = _pins(TESTS / "requirements.txt")
    runtime = {}
    for req in sorted(ROOT.glob("services/**/requirements.txt")):
        for pkg, version in _pins(req).items():
            runtime.setdefault(pkg, {})[req.relative_to(ROOT).as_posix()] = version
    for pkg, version in proofs.items():
        for req, theirs in runtime.get(pkg, {}).items():
            assert theirs == version, f"{pkg}: proofs pin {version}, {req} pins {theirs}"
