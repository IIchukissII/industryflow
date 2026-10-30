# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests that need the capability store run on a real Redis in CI (industryflow_contracts.testing).
Both sides fail silently: a test that builds its own fake is green on a timeout a real server raises
(#501), and a CI job that stops providing the server, or stops requiring it, falls back to the fake
without a word."""
import re

from _repo import ROOT, load_yaml

from industryflow_contracts import testing

HELPER = ROOT / "libs" / "industryflow_contracts" / "industryflow_contracts" / "testing.py"
FAKE_CLIENT = re.compile(r"\bfakeredis\.(?:FakeRedis|FakeAsyncRedis|FakeStrictRedis|FakeServer|aioredis)\b")


def _tests():
    for path in sorted(ROOT.rglob("test*.py")):
        rel = path.relative_to(ROOT).as_posix()
        if "/node_modules/" not in rel and not rel.startswith((".venv", "build/")):
            yield rel, path.read_text(errors="ignore")


def test_no_test_builds_its_own_fake_store():
    helper = HELPER.relative_to(ROOT).as_posix()
    offenders = [rel for rel, src in _tests() if rel != helper and FAKE_CLIENT.search(src)]
    assert not offenders, f"take the store from industryflow_contracts.testing: {offenders}"


def test_the_unit_test_job_runs_a_real_store_and_requires_it():
    job = load_yaml(".github/workflows/unit-tests.yml")["jobs"]["pytest"]
    assert "redis" in job.get("services", {}), "the pytest job runs no Redis service"
    env = job.get("env", {})
    assert env.get(testing.URL_ENV, "").startswith("redis://localhost:6379")
    assert str(env.get(testing.REQUIRE_ENV)) == "1"
