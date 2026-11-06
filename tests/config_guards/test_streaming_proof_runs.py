# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The streaming job's real-store proof runs, on the platform's own stores, and its result gates
(services/spark_jobs/tests/test_streaming_integration.py, ADR-0026 dec 11).

Every side fails silently: a proof left in the no-stack matrix only skips; a job that stops starting
a store, building the image or requiring the stack skips or proves nothing; a job the gate does not
read can fail without blocking a merge.
"""
import re

from _repo import ROOT, load_yaml

PROOF = "services/spark_jobs/tests/test_streaming_integration.py"
WORKFLOW = ".github/workflows/unit-tests.yml"
JOB = "spark-streaming"


def _jobs():
    return load_yaml(WORKFLOW)["jobs"]


def _runs(job):
    return "\n".join(s.get("run", "") for s in job["steps"])


def test_the_proof_exists_and_is_an_integration_module():
    src = (ROOT / PROOF).read_text()
    assert re.search(r"^pytestmark = pytest\.mark\.integration", src, re.M)
    assert re.search(r'^REQUIRE_ENV = "IF_REQUIRE_LIVE_STACK"', src, re.M)


def test_the_no_stack_matrix_leaves_the_proof_out():
    entry = next(e for e in _jobs()["pytest"]["strategy"]["matrix"]["include"] if e["service"] == "spark_jobs")
    assert f"--ignore={PROOF}" in entry.get("args", "")


def test_the_job_starts_every_store_builds_the_image_and_requires_the_stack():
    job = _jobs()[JOB]
    runs = _runs(job)
    ups = "\n".join(line for line in runs.splitlines() if re.search(r"docker compose up\b", line))
    # The job's executors run on the worker, as deployed; without one the application only waits.
    for service in ("kafka", "timescaledb", "minio", "spark-master", "spark-worker"):
        assert re.search(rf"\b{service}\b", ups), f"the job does not start {service}"
    assert "docker compose run --rm db-migrate" in runs, "the job applies no schema"
    assert "minio-init" in runs, "the job creates no checkpoint bucket"
    assert "docker compose build spark-streaming" in runs, "the job does not build the image it runs"
    step = next(s for s in job["steps"] if "pytest" in s.get("run", ""))
    assert PROOF in step["run"]
    assert str(step.get("env", {}).get("IF_REQUIRE_LIVE_STACK")) == "1", "a missing stack would skip"


def test_the_required_gate_reads_the_job():
    gate = _jobs()["unit-tests-gate"]
    assert JOB in gate["needs"]
    script = gate["steps"][0]["run"]
    match = re.search(rf'(\w+)="\$\{{\{{ needs\.{JOB}\.result \}}\}}"', script)
    assert match, "the gate never reads the job's result"
    assert re.search(rf'for r in [^;]*"\${match.group(1)}"', script), "the gate reads the result but never tests it"
