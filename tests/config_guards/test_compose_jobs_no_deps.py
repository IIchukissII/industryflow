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

import re

from _repo import ROOT, load_yaml

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


# A one-off run of a service that is not run-only (the user CLI in an api-gateway container, the runner's
# `status`) keeps its dependencies for `up`, so its command carries `--no-deps`. The repository's own
# commands are checked here; those of the manuals held outside it are not.
SOURCES = [*sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "scripts").glob("*.sh")), ROOT / "README.md"]
# `docker compose [global options] run [options] <service>`: run as the subcommand, up to the command's end
# (a table cell's edge, a line end, `&&` or `;`), so two commands on one line are checked one by one.
SHELL_RUN = re.compile(r"docker compose(?:\s+(?:--profile|-f|-p|--env-file)\s+\S+)*\s+run\b[^|\n&;`]*")


def _target(command: str) -> str | None:
    """The service a `run` names: the first word after `run` and its options that is a service."""
    words = [w.strip("()\"'") for w in command.split()]
    return next((w for w in words[words.index("run") + 1:] if w in SERVICES), None)


def test_the_text_guard_reads_invocations():
    found = [m.group(0) for p in SOURCES for m in SHELL_RUN.finditer(p.read_text())]
    assert any(_target(c) == "api-gateway" for c in found), found


def test_every_repository_run_of_a_stack_service_skips_dependencies():
    bad = sorted(f"{p.relative_to(ROOT)}: {m.group(0).strip()}" for p in SOURCES
                 for m in SHELL_RUN.finditer(p.read_text())
                 if _target(m.group(0)) not in JOBS and "--no-deps" not in m.group(0))
    assert not bad, f"compose runs that bring the stack's datastores to their declared state: {bad}"


def test_the_e2e_harness_runs_jobs_without_dependencies():
    text = (ROOT / "tests" / "e2e" / "harness.py").read_text()
    job = text[text.index("def job("):text.index("def script(")]
    assert '"run", "--rm", "--no-deps"' in job, job
