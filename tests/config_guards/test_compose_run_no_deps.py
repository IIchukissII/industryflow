# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every one-off job is run against the running stack with --no-deps.

`docker compose run` starts a job's dependencies, which re-runs the one-shot steps they chain to
(db-cert-init, minio-data-owner), and Compose then recreates TimescaleDB and MinIO behind them: on the
box each deletion job and each nightly backup restarted the database and object store under the live
stream. The documented commands, the scripts and the e2e harness are where operators copy them from.
"""

from __future__ import annotations

import re

from _repo import ROOT

SOURCES = [*sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "scripts").glob("*.sh")),
           ROOT / "tests" / "e2e" / "harness.py", ROOT / "README.md"]
# `docker compose [global options] run …`: run as the subcommand, up to the command's end (a table
# cell's edge, a line end, `&&` or `;`), so two commands on one line are checked one by one.
SHELL_RUN = re.compile(r"docker compose(?:\s+(?:--profile|-f|-p|--env-file)\s+\S+)*\s+run\b[^|\n&;`]*")


def test_the_guard_reads_invocations():
    found = [m.group(0) for p in SOURCES if p.suffix != ".py" for m in SHELL_RUN.finditer(p.read_text())]
    assert len(found) >= 8, found


def test_every_documented_or_scripted_run_skips_dependencies():
    bad = sorted(f"{p.relative_to(ROOT)}: {m.group(0).strip()}"
                 for p in SOURCES if p.suffix != ".py"
                 for m in SHELL_RUN.finditer(p.read_text()) if "--no-deps" not in m.group(0))
    assert not bad, f"compose runs that restart the stack's datastores: {bad}"


def test_the_e2e_harness_runs_jobs_without_dependencies():
    text = (ROOT / "tests" / "e2e" / "harness.py").read_text()
    job = text[text.index("def job("):text.index("def script(")]
    assert '"run", "--rm", "--no-deps"' in job, job
