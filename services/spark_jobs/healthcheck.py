# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Container health for a Spark job: healthy only when the job can actually run.

A process check cannot tell a running job from one whose application sits WAITING on the master
with no executor (for example when two jobs together ask for more executor memory than the worker
has): the driver process is alive, the container reports healthy, and nothing is processed.

This asks the driver's own UI (the REST API on ``SPARK_UI_URL``, default ``http://localhost:4040``)
and is healthy only if an application is up and has compute: in ``local`` mode the driver runs the
tasks itself; against a cluster master at least one active executor with cores must be attached.
Standard library only: it runs in the job image, outside the job's interpreter.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request

TIMEOUT_SECONDS = 5


def _get(base: str, path: str):
    with urllib.request.urlopen(f"{base}{path}", timeout=TIMEOUT_SECONDS) as response:
        return json.load(response)


def verdict(applications: list, executors: list, master: str) -> tuple[bool, str]:
    """Pure decision from the UI's answers; the I/O is in :func:`main`."""
    if not applications:
        return False, "the driver UI lists no application"
    if master.startswith("local"):
        driver = [e for e in executors if e.get("id") == "driver" and e.get("isActive")]
        return (True, "local mode, driver active") if driver else (False, "local mode, driver not active")
    workers = [
        e for e in executors
        if e.get("id") != "driver" and e.get("isActive") and int(e.get("totalCores") or 0) > 0
    ]
    if not workers:
        return False, "no active executor with cores: the application is waiting for resources"
    return True, f"{len(workers)} active executor(s)"


def main() -> int:
    base = os.environ.get("SPARK_UI_URL", "http://localhost:4040").rstrip("/")
    master = os.environ.get("SPARK_MASTER", "local[*]")
    try:
        applications = _get(base, "/api/v1/applications")
        executors = _get(base, f"/api/v1/applications/{applications[0]['id']}/executors") if applications else []
    except Exception as exc:  # unreachable UI, bad JSON, timeout: all unhealthy
        print(f"unhealthy: driver UI not answering ({exc.__class__.__name__})")
        return 1
    healthy, reason = verdict(applications, executors, master)
    print(("healthy: " if healthy else "unhealthy: ") + reason)
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
