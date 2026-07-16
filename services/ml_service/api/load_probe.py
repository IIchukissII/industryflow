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

This module decides; ``load_probe_worker`` observes, in a process of its own. The split is not
ceremony: the worker's environment refuses object-deserialisation, and that switch is process-wide —
set in the service it would silently bind the notebook path too, which ADR-0030 dec 4 deliberately
did not decide.

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
from typing import Optional

logger = logging.getLogger(__name__)

# A load and one row. Generous enough for a large artifact to come off the object store, short enough
# that a hanging load is a refusal rather than a stuck deployment.
PROBE_TIMEOUT_SECONDS = int(os.getenv("UPLOAD_PROBE_TIMEOUT_SECONDS", "120"))

_WORKER = Path(__file__).with_name("load_probe_worker.py")


@dataclass(frozen=True)
class ProbeResult:
    """What was observed, and — if it did not work — something an operator can act on."""

    ok: bool
    stage: str = ""
    error: str = ""

    @property
    def refused(self) -> bool:
        return not self.ok


def _worker_env() -> dict:
    env = dict(os.environ)
    # ADR-0030 dec 4, the half a structural check cannot reach: MLflow will deserialise author
    # objects unless told not to, and it defaults to yes. Bound to this process so the upload path
    # gets the refusal and the notebook path keeps its deferral — the audience distinction, honoured
    # one layer down.
    env["MLFLOW_ALLOW_PICKLE_DESERIALIZATION"] = "false"
    return env


def _run(model_uri: str) -> ProbeResult:
    try:
        proc = subprocess.run(
            [sys.executable, str(_WORKER), model_uri],
            capture_output=True, text=True, timeout=PROBE_TIMEOUT_SECONDS,
            env=_worker_env(), cwd=str(_WORKER.parent),
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(False, "timeout",
                           f"loading and scoring this artifact did not finish within "
                           f"{PROBE_TIMEOUT_SECONDS}s, so it has not been shown to work here")
    except OSError as exc:
        return ProbeResult(False, "invoke", f"could not run the check: {exc}")

    line = (proc.stdout or "").strip().splitlines()
    try:
        payload = json.loads(line[-1]) if line else {}
    except ValueError:
        payload = {}
    if not payload:
        # It died in a way it could not report — a segfault, an OOM kill, an import that took the
        # interpreter down. That is exactly the class of failure the process boundary exists to
        # survive, and it is a refusal.
        tail = (proc.stderr or "").strip().splitlines()[-1:] or ["no output"]
        return ProbeResult(False, "crash",
                           f"the check ended without a verdict (exit {proc.returncode}): {tail[0]}")

    return ProbeResult(bool(payload.get("ok")), str(payload.get("stage", "")),
                       str(payload.get("error", "")))


async def probe(model_uri: str) -> ProbeResult:
    """Load and score ``model_uri`` in an isolated copy of this environment, off the event loop."""
    return await asyncio.to_thread(_run, model_uri)
