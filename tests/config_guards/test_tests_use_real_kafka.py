# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The real-broker proofs run, on the platform's broker, and their result gates (ADR-0026 dec 11):
the alert worker's consumer loop (services/alert_service/tests/test_kafka_consumer_integration.py),
ingestion's producer and the tenant-status stream (services/ingestion_service/tests/test_*_integration.py).

Every side fails silently: a proof left in the no-broker matrix only skips; a job that stops
starting the broker, or stops requiring it, skips too; a job the gate does not read can fail
without blocking a merge; a real-broker module the job does not name never runs at all.
"""
import re

import pytest

from _repo import ROOT, load_yaml

WORKFLOW = ".github/workflows/unit-tests.yml"
JOB = "alert-kafka"
# Each service's real-broker proofs: its tests directory and the proofs it must hold. A proof names the
# broker's environment variables itself, or takes them with require_broker from the service's shared
# helper (HELPER); either way the guard reads the names from the file that declares them.
SERVICES = {
    "alert_service": ("services/alert_service/tests", ["test_kafka_consumer_integration.py"]),
    "ingestion_service": ("services/ingestion_service/tests",
                          ["test_kafka_producer_integration.py", "test_tenant_status_integration.py"]),
}
HELPER = "real_kafka"


def _module_constant(src: str, name: str) -> str:
    match = re.search(rf'^{name} = "([^"]+)"', src, re.M)
    assert match, f"no {name} constant"
    return match.group(1)


def _env_names(module: str) -> tuple[str, str]:
    """(REQUIRE_ENV, BOOTSTRAP_ENV) as the module's broker check reads them."""
    src = (ROOT / module).read_text()
    if re.search(r'^REQUIRE_ENV = "', src, re.M):
        return _module_constant(src, "REQUIRE_ENV"), _module_constant(src, "BOOTSTRAP_ENV")
    imported = re.search(rf"^from {HELPER} import [^\n]*\brequire_broker\b", src, re.M)
    assert imported, f"{module} declares no broker requirement the guard can read"
    helper = (ROOT / module).parent / f"{HELPER}.py"
    assert helper.exists(), f"{module} imports {HELPER}, which is not beside it"
    src = helper.read_text()
    return _module_constant(src, "REQUIRE_ENV"), _module_constant(src, "BOOTSTRAP_ENV")


def _integration_modules(tests: str) -> list[str]:
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / tests).glob("test_*.py")
        if re.search(r"^pytestmark = pytest\.mark\.integration", p.read_text(), re.M)
    )


def _jobs():
    return load_yaml(WORKFLOW)["jobs"]


@pytest.mark.parametrize("service", SERVICES)
def test_there_are_the_real_broker_proofs(service):
    tests, proofs = SERVICES[service]
    missing = [f"{tests}/{p}" for p in proofs if f"{tests}/{p}" not in _integration_modules(tests)]
    assert not missing, f"not an integration module, or gone: {missing}"


@pytest.mark.parametrize("service", SERVICES)
def test_the_no_broker_matrix_leaves_every_real_broker_module_out(service):
    tests = SERVICES[service][0]
    entry = next(e for e in _jobs()["pytest"]["strategy"]["matrix"]["include"] if e["service"] == service)
    missing = [m for m in _integration_modules(tests) if f"--ignore={m}" not in entry.get("args", "")]
    assert not missing, f"would only skip in the no-broker matrix: {missing}"


def test_the_kafka_job_starts_the_compose_broker():
    runs = [s.get("run", "") for s in _jobs()[JOB]["steps"]]
    assert any(re.search(r"docker compose up\b.*\bkafka\b", r) for r in runs), "the job starts no broker"


@pytest.mark.parametrize("service", SERVICES)
def test_the_kafka_job_runs_every_real_broker_module_and_requires_the_broker(service):
    steps = [s for s in _jobs()[JOB]["steps"] if "pytest" in s.get("run", "")]
    for module in _integration_modules(SERVICES[service][0]):
        running = [s for s in steps if module in s["run"]]
        assert running, f"the kafka job does not run {module}"
        require, bootstrap = _env_names(module)
        for step in running:
            env = step.get("env", {})
            assert str(env.get(require)) == "1", f"a missing broker would skip {module}: {step['name']}"
            assert env.get(bootstrap) == "localhost:9092", f"{module}: {step['name']}"


def test_the_required_gate_reads_the_kafka_job():
    gate = _jobs()["unit-tests-gate"]
    assert JOB in gate["needs"]
    script = gate["steps"][0]["run"]
    assert f"needs.{JOB}.result" in script
    # The result read must be one the failure check actually tests.
    var = re.search(rf'(\w+)="\$\{{\{{ needs\.{JOB}\.result \}}\}}"', script).group(1)
    assert re.search(rf'for r in [^;]*"\${var}"', script), "the gate reads the job's result but never tests it"
