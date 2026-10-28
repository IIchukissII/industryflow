# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The empirical check for an artifact nothing watched being made (ADR-0030 dec 7).

ADR-0027 dec 5 requires two checks and makes the **empirical** one the authority: a model trained by
the real authoring image must load, and score, in the real serving image. An uploaded artifact has no
authoring image, so that round-trip cannot run — one of its two ends does not exist.

It is **replaced, not dropped**. The substitute is the half that can still be observed: does *this*
environment load this artifact and get a number out of it? That is weaker, and the weakness must be
said plainly rather than dressed up — it proves the artifact works **here**, not that **here**
resembles **where it was made**. Its value is that it turns the one thing still observable into
evidence, instead of accepting a stranger's word for both halves. Dropping it to a version
comparison would leave assertions checked against assertions, which ADR-0027 dec 5 rejects.

This module decides; ``load_probe_worker`` observes, in a process of its own, so an artifact that
crashes or hangs its loader takes down the probe rather than the service. Both processes keep
object-deserialisation off for their whole life (ADR-0037 dec 3).

**A probe that could not run is not a probe that passed.** Every failure mode here — crash, timeout,
unreadable output — resolves to a refusal, because the gate's question is "has this been shown to
work here", and silence is not a yes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)

# A load and one row. Generous enough for a large artifact to come off the object store, short enough
# that a hanging load is a refusal rather than a stuck deployment.
PROBE_TIMEOUT_SECONDS = int(os.getenv("UPLOAD_PROBE_TIMEOUT_SECONDS", "120"))
# Scoring a feature sample row by row comes after the verdict, on a budget of its own.
SAMPLE_TIMEOUT_SECONDS = int(os.getenv("UPLOAD_PROBE_SAMPLE_TIMEOUT_SECONDS", "300"))

_WORKER = Path(__file__).with_name("load_probe_worker.py")


@dataclass(frozen=True)
class ProbeResult:
    """What was observed, and — if it did not work — something an operator can act on."""

    ok: bool
    stage: str = ""
    error: str = ""
    # The detector's scores on the model's feature sample, when one was given (ADR-0035 dec 2), or why
    # the detector could not score it. Neither decides whether the model may deploy.
    scores: Optional[List[float]] = None
    sample_error: str = ""

    @property
    def refused(self) -> bool:
        return not self.ok


# What the probe is given of this service's environment: enough to run Python and to READ the
# artifact from the object store, and nothing else. The probe loads a stranger's artifact, so it is
# the one process here that must not hold the database password, the token-signing key, the internal
# service token or the capability store's address — a load that runs author code would have them.
# An allowlist, so a secret added to the service later is withheld by default rather than inherited.
_PROBE_ENV = (
    # the interpreter
    "PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "PYTHONPATH", "PYTHONUNBUFFERED",
    # read access to the artifact store
    "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
    "AWS_DEFAULT_REGION", "AWS_REGION", "AWS_CA_BUNDLE",
    "MLFLOW_S3_ENDPOINT_URL", "MLFLOW_S3_IGNORE_TLS",
    # trust roots for that TLS connection
    "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE",
    # the operator's detector extensions, to score a feature sample as serving would (ADR-0035 dec 2)
    "EXTENSION_MODULES",
)


def _worker_env() -> dict:
    env = {name: os.environ[name] for name in _PROBE_ENV if name in os.environ}
    # The half a structural check cannot reach: MLflow deserialises author objects unless told not to,
    # and it defaults to yes. Off before the worker imports anything (ADR-0037 dec 3).
    env["MLFLOW_ALLOW_PICKLE_DESERIALIZATION"] = "false"
    return env


def _lines(out) -> list:
    """The worker's JSON lines. A timeout hands back partial output, as bytes whatever the mode."""
    if isinstance(out, bytes):
        out = out.decode("utf-8", "replace")
    parsed = []
    for line in (out or "").strip().splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            parsed.append(value)
    return parsed


def _result(lines: list, stalled: str) -> Optional[ProbeResult]:
    """The verdict the worker printed, with its sample's outcome; None if it printed no verdict.

    The verdict comes first and the sample is scored after it, so a load that was shown to work stays
    a pass when scoring the sample then stalls or dies (ADR-0035 dec 5: a baseline never decides the
    deployment). ``stalled`` says what happened to the sample if no line reports it.
    """
    verdict = next((v for v in lines if "ok" in v), None)
    if verdict is None:
        return None
    scored = next((v for v in lines if "scores" in v or "sample_error" in v), {})
    scores = scored.get("scores")
    return ProbeResult(bool(verdict.get("ok")), str(verdict.get("stage", "")), str(verdict.get("error", "")),
                       scores=[float(v) for v in scores] if isinstance(scores, list) else None,
                       sample_error=str(scored.get("sample_error", "")) or (stalled if not scored else ""))


def _run(model_uri: str, sample: Optional[dict] = None) -> ProbeResult:
    budget = PROBE_TIMEOUT_SECONDS + (SAMPLE_TIMEOUT_SECONDS if sample else 0)
    try:
        proc = subprocess.run(
            [sys.executable, str(_WORKER), model_uri],
            input=json.dumps(sample) if sample else "",
            capture_output=True, text=True, timeout=budget,
            env=_worker_env(), cwd=str(_WORKER.parent),
        )
    except subprocess.TimeoutExpired as exc:
        result = _result(_lines(exc.stdout), f"scoring the feature sample did not finish within {budget}s")
        if result is not None:
            return result
        return ProbeResult(False, "timeout",
                           f"loading and scoring this artifact did not finish within "
                           f"{PROBE_TIMEOUT_SECONDS}s, so it has not been shown to work here")
    except OSError as exc:
        return ProbeResult(False, "invoke", f"could not run the check: {exc}")

    tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
    result = _result(_lines(proc.stdout), f"scoring the feature sample ended without a result "
                                          f"(exit {proc.returncode}): {tail[0]}")
    if result is None:
        # It died in a way it could not report — a segfault, an OOM kill, an import that took the
        # interpreter down. That is exactly the class of failure the process boundary exists to
        # survive, and it is a refusal.
        return ProbeResult(False, "crash",
                           f"the check ended without a verdict (exit {proc.returncode}): {tail[0]}")
    return result


async def probe(model_uri: str, sample: Optional[dict] = None) -> ProbeResult:
    """Load and score ``model_uri`` in an isolated copy of this environment, off the event loop; with
    ``sample`` (engineered rows, the detector and its context), also score those rows in that load."""
    return await asyncio.to_thread(_run, model_uri, sample)
