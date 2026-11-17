# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A one-off job cannot restart the stack's datastores, however it is run.

`docker compose run` brings a job's dependency chain to its declared state: it re-runs the exited
one-shot steps (db-cert-init re-ran on the box for a plain `run`, measured 2026-11-17) and recreates
TimescaleDB or MinIO when their configuration changed since they started; on the box, deletion jobs and
nightly backups restarted the database and object store under the live stream that way. A run-only job
(a profile, restart "no") therefore depends only on run-only services that themselves declare no
dependency (its ledger check, ADR-0034 dec 10), so a command copied without `--no-deps`, from any
runbook or a hand-typed line, starts nothing of the running stack.
"""

from __future__ import annotations

from _repo import load_yaml

SERVICES = load_yaml("docker-compose.yml")["services"]
JOBS = sorted(n for n, s in SERVICES.items() if s.get("profiles") and s.get("restart") in ("no", False))


def test_the_guard_reads_the_jobs():
    """Re-read when a job is added or a profile renamed: these are the jobs the box ran and restarted."""
    assert {"db-backup", "restore-rehearsal", "notebook-backup", "notebook-rehearsal", "cold-export",
            "tenant-delete", "tracking-collect"} <= set(JOBS), JOBS


def test_a_run_only_job_starts_nothing_of_the_running_stack():
    bad = sorted(f"{n} -> {d}" for n in JOBS for d in SERVICES[n].get("depends_on") or {}
                 if d not in JOBS or SERVICES[d].get("depends_on"))
    assert not bad, f"run-only jobs whose run would bring the stack's datastores to their declared state: {bad}"
