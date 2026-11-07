# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The Spark jobs' real-store proofs run, on the platform's own stores, and their result gates
(services/spark_jobs/tests/*_integration.py on spark_stack.py, ADR-0026 dec 11).

Every side fails silently: a proof left in the no-stack matrix only skips; a job that stops starting
a store, building the image or requiring the stack skips or proves nothing; a job the gate does not
read can fail without blocking a merge.
"""
import re

from _repo import ROOT, gate_tests_every_need, load_yaml

TESTS = "services/spark_jobs/tests"
HELPER = f"{TESTS}/spark_stack.py"
WORKFLOW = ".github/workflows/unit-tests.yml"
JOB = "spark-streaming"


def _jobs():
    return load_yaml(WORKFLOW)["jobs"]


def _runs(job):
    return "\n".join(s.get("run", "") for s in job["steps"])


def _proofs():
    return sorted(
        p.relative_to(ROOT).as_posix() for p in (ROOT / TESTS).glob("test_*.py")
        if re.search(r"^pytestmark = pytest\.mark\.integration", p.read_text(), re.M)
    )


def _services(proof):
    """The compose services a proof runs as its job (spark_stack.Job's service argument)."""
    return set(re.findall(r'(?:Job|__init__)\(tenant, "([a-z-]+)"', (ROOT / proof).read_text()))


def test_both_jobs_have_a_proof_on_the_shared_stack():
    assert {s for p in _proofs() for s in _services(p)} >= {"spark-streaming", "spark-aggregations"}
    assert re.search(r'^REQUIRE_ENV = "IF_REQUIRE_LIVE_STACK"', (ROOT / HELPER).read_text(), re.M)
    for proof in _proofs():
        src = (ROOT / proof).read_text()
        # The stack fixture is what turns a missing stack into a failure under the job's env.
        assert "spark_stack.require_stack()" in src, f"{proof} does not require the stack"


def test_the_no_stack_matrix_leaves_every_proof_out():
    entry = next(e for e in _jobs()["pytest"]["strategy"]["matrix"]["include"] if e["service"] == "spark_jobs")
    missing = [p for p in _proofs() if f"--ignore={p}" not in entry.get("args", "")]
    assert not missing, f"would only skip in the no-stack matrix: {missing}"


def test_the_job_starts_every_store_builds_the_image_and_requires_the_stack():
    job = _jobs()[JOB]
    runs = _runs(job)
    ups = "\n".join(line for line in runs.splitlines() if re.search(r"docker compose up\b", line))
    # The job's executors run on the worker, as deployed; without one the application only waits.
    for service in ("kafka", "timescaledb", "minio", "spark-master", "spark-worker"):
        assert re.search(rf"(?<![\w-]){service}(?![\w-])", ups), f"the job does not start {service}"
    assert "docker compose run --rm db-migrate" in runs, "the job applies no schema"
    # An attached `up` exits 0 whatever the one-shot did; `run` exits with it.
    assert "docker compose run --rm --no-deps minio-init" in runs, "a failed bucket init would pass"
    build = next(line for line in runs.splitlines() if "docker compose build" in line)
    for service in sorted({s for p in _proofs() for s in _services(p)}):
        assert re.search(rf"(?<![\w-]){service}(?![\w-])", build), f"the job does not build {service}, which a proof runs"
    step = next(s for s in job["steps"] if "pytest" in s.get("run", ""))
    missing = [p for p in _proofs() if p not in step["run"]]
    assert not missing, f"the job does not run: {missing}"
    assert str(step.get("env", {}).get("IF_REQUIRE_LIVE_STACK")) == "1", "a missing stack would skip"
    assert job.get("needs") == "changes" and "relevant" in job.get("if", ""), "the job is not path-gated as the gate expects"


def test_the_required_gate_reads_the_job():
    gate = _jobs()["unit-tests-gate"]
    assert JOB in gate["needs"]
    assert gate_tests_every_need(gate), "the gate does not test every result in needs"
