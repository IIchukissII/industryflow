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


# --- the worker's environment refuses object-deserialisation ------------------------------------

def test_the_worker_is_told_not_to_deserialise_author_objects():
    env = load_probe._worker_env()
    assert env["MLFLOW_ALLOW_PICKLE_DESERIALIZATION"] == "false"


def test_the_worker_is_told_so_even_where_this_process_was_configured_otherwise(monkeypatch):
    """The worker's own environment carries the refusal (ADR-0037 dec 3), whatever this one holds."""
    monkeypatch.setenv("MLFLOW_ALLOW_PICKLE_DESERIALIZATION", "true")
    assert load_probe._worker_env()["MLFLOW_ALLOW_PICKLE_DESERIALIZATION"] == "false"


def test_the_worker_reaches_the_same_object_store(monkeypatch):
    # It must reach the same object store the serving path does; a probe in a different environment
    # would prove the wrong thing. (Only what reading the artifact needs is passed — see
    # test_silent_omission_guards for the secrets it is not handed.)
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


# --- the sample never decides the verdict (ADR-0035 dec 5) ----------------------------------------

def test_a_sample_that_stalls_after_a_good_load_is_not_a_refusal(monkeypatch):
    def stall(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=1,
                                        output=b'{"ok": true, "stage": "score"}\n')
    monkeypatch.setattr(load_probe.subprocess, "run", stall)
    result = load_probe._run("models:/m", {"detector": "sklearn", "rows": [[1.0]]})
    assert result.ok and result.scores is None
    assert "did not finish" in result.sample_error


def test_a_crash_while_scoring_after_a_good_load_is_not_a_refusal(monkeypatch):
    _run_returns(monkeypatch, stdout='{"ok": true, "stage": "score"}\n', returncode=-9, stderr="Killed")
    result = load_probe._run("models:/m", {"detector": "sklearn", "rows": [[1.0]]})
    assert result.ok and "ended without a result" in result.sample_error


def test_a_stall_before_any_verdict_is_still_a_refusal(monkeypatch):
    def stall(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="worker", timeout=1, output=b"")
    monkeypatch.setattr(load_probe.subprocess, "run", stall)
    result = load_probe._run("models:/m", {"detector": "sklearn", "rows": [[1.0]]})
    assert result.refused and result.stage == "timeout"


def test_the_sample_line_carries_the_scores(monkeypatch):
    _run_returns(monkeypatch, stdout='{"ok": true, "stage": "score"}\n{"scores": [0.1, 0.2]}\n')
    assert load_probe._run("models:/m", {"rows": [[1.0], [2.0]]}).scores == [0.1, 0.2]
