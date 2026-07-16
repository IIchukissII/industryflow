# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The empirical check's decision layer (ADR-0030 dec 7).

The worker's job is to observe; this module's job is to decide, and the property under test is the
one that makes the check worth having: **a probe that could not run is not a probe that passed.**
The gate asks "has this been shown to work here", and every way of not answering — a crash, a hang,
unreadable output — is a no. A check that fails open is a check that reports the environment is fine
precisely when it is least able to know.
"""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "api"))

import load_probe  # noqa: E402


class _Proc:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout, self.stderr, self.returncode = stdout, stderr, returncode


def _run_returns(monkeypatch, **kw):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc(**kw))


def _run_raises(monkeypatch, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(subprocess, "run", boom)


# --- it worked ---------------------------------------------------------------------------------

def test_a_load_and_score_that_worked_is_admitted(monkeypatch):
    _run_returns(monkeypatch, stdout=json.dumps({"ok": True, "stage": "score", "output_type": "ndarray"}))
    r = load_probe._run("s3://b/tenant_x/uploads/u1")
    assert r.ok and not r.refused


# --- it did not work, and said so ---------------------------------------------------------------

@pytest.mark.parametrize("stage", ["manifest", "signature", "load", "score"])
def test_each_reported_failure_is_a_refusal(monkeypatch, stage):
    _run_returns(monkeypatch, stdout=json.dumps({"ok": False, "stage": stage, "error": "nope"}))
    r = load_probe._run("s3://b/x")
    assert r.refused
    assert r.stage == stage
    assert r.error == "nope"          # carried through, so an operator can act on it


# --- it could not run, which is NOT a pass ------------------------------------------------------

def test_a_hang_is_a_refusal_not_a_pass(monkeypatch):
    _run_raises(monkeypatch, subprocess.TimeoutExpired(cmd="x", timeout=1))
    r = load_probe._run("s3://b/x")
    assert r.refused
    assert r.stage == "timeout"
    assert "not been shown to work here" in r.error


def test_a_crash_with_no_verdict_is_a_refusal(monkeypatch):
    # A segfault or an OOM kill: the process dies without reporting. This is the class of failure the
    # process boundary exists to survive, and surviving it means refusing.
    _run_returns(monkeypatch, stdout="", stderr="Killed", returncode=-9)
    r = load_probe._run("s3://b/x")
    assert r.refused
    assert r.stage == "crash"


def test_unreadable_output_is_a_refusal(monkeypatch):
    _run_returns(monkeypatch, stdout="not json at all", returncode=0)
    assert load_probe._run("s3://b/x").refused


def test_an_empty_verdict_is_a_refusal(monkeypatch):
    _run_returns(monkeypatch, stdout="{}", returncode=0)
    assert load_probe._run("s3://b/x").refused


def test_a_zero_exit_with_no_output_is_still_a_refusal(monkeypatch):
    # The dangerous shape: exit 0 looks like success to anything that only checks the code.
    _run_returns(monkeypatch, stdout="", returncode=0)
    assert load_probe._run("s3://b/x").refused


def test_being_unable_to_start_the_check_is_a_refusal(monkeypatch):
    _run_raises(monkeypatch, OSError("no interpreter"))
    r = load_probe._run("s3://b/x")
    assert r.refused
    assert r.stage == "invoke"


# --- the worker's environment refuses object-deserialisation, and only the worker's -------------

def test_the_worker_is_told_not_to_deserialise_author_objects():
    env = load_probe._worker_env()
    assert env["MLFLOW_ALLOW_PICKLE_DESERIALIZATION"] == "false"


def test_that_switch_is_not_set_on_this_process():
    """It is process-wide, so setting it in the service would silently bind the notebook path too —
    a refusal ADR-0030 dec 4 deliberately did not decide for kernels."""
    load_probe._worker_env()
    assert "MLFLOW_ALLOW_PICKLE_DESERIALIZATION" not in os.environ


def test_the_worker_inherits_the_rest_of_the_environment(monkeypatch):
    # It must reach the same object store the serving path does; a probe in a different environment
    # would prove the wrong thing.
    monkeypatch.setenv("MLFLOW_S3_ENDPOINT_URL", "http://minio:9000")
    assert load_probe._worker_env()["MLFLOW_S3_ENDPOINT_URL"] == "http://minio:9000"


# --- it runs the real worker, in a real separate process ---------------------------------------

def test_the_worker_script_exists_and_is_what_gets_run():
    assert load_probe._WORKER.exists()
    assert load_probe._WORKER.name == "load_probe_worker.py"


def test_the_worker_refuses_a_uri_it_cannot_read_and_says_so_as_json():
    """Really spawns it — no mocks. Proves the contract between the two halves: the worker reports a
    fact as JSON, and does not take the caller down with it."""
    proc = subprocess.run(
        [sys.executable, str(load_probe._WORKER), "s3://nonexistent-bucket/nothing/here"],
        capture_output=True, text=True, timeout=120, cwd=str(load_probe._WORKER.parent),
        env={**os.environ, "MLFLOW_ALLOW_PICKLE_DESERIALIZATION": "false"},
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert payload["stage"] == "manifest"
    assert payload["error"]


def test_the_worker_refuses_being_called_without_a_uri():
    proc = subprocess.run([sys.executable, str(load_probe._WORKER)],
                          capture_output=True, text=True, timeout=60)
    assert json.loads(proc.stdout.strip())["ok"] is False
