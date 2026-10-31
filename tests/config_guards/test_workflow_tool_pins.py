# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""What a workflow runs on and installs is pinned where the dependency bot sees it.

An inline `pip install pkg==x` in a workflow is a pin no bot reads: it stays at x while every
requirements file moves on. A floating runner label changes the operating system under every job
on the provider's date, not ours. A gate whose path filter misses the tool file it installs never
runs on that file's bump.
"""

from __future__ import annotations

import re

import yaml

from _repo import ROOT, covered, load_yaml

TOOLS = "scripts/ci/requirements.txt"
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
# `pkg==1.2` in a pip install line; a version from a shell variable is a deliberate input (the
# previous hub release a migration test seeds from), not a pin.
INLINE_PIN = re.compile(r"pip install[^\n]*?\s['\"]?[A-Za-z0-9_.\[\],-]+==(?!\$)[0-9]")
RUNNER = re.compile(r"ubuntu-\d{2}\.\d{2}")


def _jobs():
    for path in WORKFLOWS:
        for name, job in (yaml.safe_load(path.read_text())["jobs"] or {}).items():
            yield path.name, name, job


def test_the_guard_reads_the_workflows():
    assert len(WORKFLOWS) >= 10 and (ROOT / TOOLS).is_file()


def test_no_workflow_pins_a_package_inline():
    found = [
        f"{path.name}:{n}: {line.strip()}"
        for path in WORKFLOWS
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if INLINE_PIN.search(line)
    ]
    assert not found, f"inline pins the bot never moves; put them in {TOOLS}: {found}"


def test_every_job_runs_on_a_pinned_runner_image():
    floating = [
        f"{wf}:{name}: {job['runs-on']}"
        for wf, name, job in _jobs()
        if "runs-on" in job and not RUNNER.fullmatch(str(job["runs-on"]))
    ]
    assert not floating, f"runner labels that are not a pinned Ubuntu release: {floating}"


def test_every_gated_job_installing_the_tools_runs_when_they_change():
    missed = []
    for wf, name, job in _jobs():
        if TOOLS not in yaml.safe_dump(job.get("steps", [])) + yaml.safe_dump(job.get("strategy", {})):
            continue
        outputs = re.findall(r"needs\.changes\.outputs\.(\w+)", str(job.get("if", "")))
        if not outputs:
            continue
        steps = load_yaml(f".github/workflows/{wf}")["jobs"]["changes"]["steps"]
        filters = yaml.safe_load(next(s for s in steps if s.get("id") == "filter")["with"]["filters"])
        if not any(covered(TOOLS, filters[o]) for o in outputs):
            missed.append(f"{wf}:{name}")
    assert not missed, f"gated jobs whose path filter misses {TOOLS}: {missed}"
