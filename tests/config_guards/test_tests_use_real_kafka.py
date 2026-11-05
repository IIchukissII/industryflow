# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert worker's real-broker proof runs, on the platform's broker, and its result gates
(services/alert_service/tests/test_kafka_consumer_integration.py, ADR-0026 dec 11).

Every side fails silently: a proof left in the no-broker matrix only skips; a job that stops
starting the broker, or stops requiring it, skips too; a job the gate does not read can fail
without blocking a merge; a second real-broker module the job does not name never runs at all.
"""
import re

from _repo import ROOT, load_yaml

TESTS = "services/alert_service/tests"
WORKFLOW = ".github/workflows/unit-tests.yml"


def _module_constant(src: str, name: str) -> str:
    match = re.search(rf'^{name} = "([^"]+)"', src, re.M)
    assert match, f"no {name} constant"
    return match.group(1)


def _integration_modules() -> list[str]:
    return sorted(
        p.relative_to(ROOT).as_posix()
        for p in (ROOT / TESTS).glob("test_*.py")
        if re.search(r"^pytestmark = pytest\.mark\.integration", p.read_text(), re.M)
    )


def _jobs():
    return load_yaml(WORKFLOW)["jobs"]


def test_there_is_a_real_broker_proof():
    assert f"{TESTS}/test_kafka_consumer_integration.py" in _integration_modules()


def test_the_no_broker_matrix_leaves_every_real_broker_module_out():
    entry = next(e for e in _jobs()["pytest"]["strategy"]["matrix"]["include"] if e["service"] == "alert_service")
    missing = [m for m in _integration_modules() if f"--ignore={m}" not in entry.get("args", "")]
    assert not missing, f"would only skip in the no-broker matrix: {missing}"


def test_the_kafka_job_starts_the_compose_broker_and_requires_it():
    job = _jobs()["alert-kafka"]
    runs = [s.get("run", "") for s in job["steps"]]
    assert any(re.search(r"docker compose up\b.*\bkafka\b", r) for r in runs), "the job starts no broker"
    step = next(s for s in job["steps"] if "pytest" in s.get("run", ""))
    src = (ROOT / TESTS / "test_kafka_consumer_integration.py").read_text()
    env = step.get("env", {})
    assert str(env.get(_module_constant(src, "REQUIRE_ENV"))) == "1", "a missing broker would skip"
    assert env.get(_module_constant(src, "BOOTSTRAP_ENV")) == "localhost:9092"
    missing = [m for m in _integration_modules() if m not in step["run"]]
    assert not missing, f"the kafka job does not run: {missing}"


def test_the_required_gate_reads_the_kafka_job():
    gate = _jobs()["unit-tests-gate"]
    assert "alert-kafka" in gate["needs"]
    script = gate["steps"][0]["run"]
    assert "needs.alert-kafka.result" in script
    # The result read must be one the failure check actually tests.
    var = re.search(r'(\w+)="\$\{\{ needs\.alert-kafka\.result \}\}"', script).group(1)
    assert re.search(rf'for r in [^;]*"\${var}"', script), "the gate reads the job's result but never tests it"
