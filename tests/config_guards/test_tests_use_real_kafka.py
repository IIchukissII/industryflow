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
# Each service's real-broker proofs: its tests directory, the module that declares the broker's
# environment variables, and the proofs it must hold.
SERVICES = {
    "alert_service": ("services/alert_service/tests", "test_kafka_consumer_integration.py",
                      ["test_kafka_consumer_integration.py"]),
    "ingestion_service": ("services/ingestion_service/tests", "real_kafka.py",
                          ["test_kafka_producer_integration.py", "test_tenant_status_integration.py"]),
}


def _module_constant(src: str, name: str) -> str:
    match = re.search(rf'^{name} = "([^"]+)"', src, re.M)
    assert match, f"no {name} constant"
    return match.group(1)


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
    tests, _, proofs = SERVICES[service]
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
    tests, constants, _ = SERVICES[service]
    modules = _integration_modules(tests)
    src = (ROOT / tests / constants).read_text()
    steps = [s for s in _jobs()[JOB]["steps"] if "pytest" in s.get("run", "")]
    missing = [m for m in modules if not any(m in s["run"] for s in steps)]
    assert not missing, f"the kafka job does not run: {missing}"
    for step in (s for s in steps if any(m in s["run"] for m in modules)):
        env = step.get("env", {})
        assert str(env.get(_module_constant(src, "REQUIRE_ENV"))) == "1", f"a missing broker would skip: {step['name']}"
        assert env.get(_module_constant(src, "BOOTSTRAP_ENV")) == "localhost:9092", step["name"]


def test_the_required_gate_reads_the_kafka_job():
    gate = _jobs()["unit-tests-gate"]
    assert JOB in gate["needs"]
    script = gate["steps"][0]["run"]
    assert f"needs.{JOB}.result" in script
    # The result read must be one the failure check actually tests.
    var = re.search(rf'(\w+)="\$\{{\{{ needs\.{JOB}\.result \}}\}}"', script).group(1)
    assert re.search(rf'for r in [^;]*"\${var}"', script), "the gate reads the job's result but never tests it"
