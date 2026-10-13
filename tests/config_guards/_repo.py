# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Readers shared by the configuration guards: the repository's workflows, compose file and chart.

Each guard reads BOTH sides of a list whose omission fails silently, so the helpers here only load
files and answer "does this path filter match that path" — they never encode a list of their own.
"""

from __future__ import annotations

import functools
import re
import shutil
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
CHART = ROOT / "deploy" / "helm" / "industryflow"


def load_yaml(rel: str):
    return yaml.safe_load((ROOT / rel).read_text())


def workflow_filter(workflow: str, job: str = "changes", step_id: str = "filter",
                    name: str = "relevant") -> list[str]:
    """The path globs one dorny/paths-filter step declares under `name`."""
    wf = load_yaml(f".github/workflows/{workflow}")
    for step in wf["jobs"][job]["steps"]:
        if step.get("id") == step_id:
            return yaml.safe_load(step["with"]["filters"])[name]
    raise AssertionError(f"{workflow}: no step id={step_id!r} in job {job!r}")


@functools.lru_cache(maxsize=None)
def _glob_regex(pattern: str) -> re.Pattern:
    # picomatch semantics as dorny/paths-filter uses them: `**` crosses directories, `*` does not.
    out, i = "", 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif pattern.startswith("**", i):
            out += ".*"
            i += 2
        elif pattern[i] == "*":
            out += "[^/]*"
            i += 1
        else:
            out += re.escape(pattern[i])
            i += 1
    return re.compile(out + r"\Z")


def covered(path: str, globs: list[str]) -> bool:
    return any(_glob_regex(g).match(path) for g in globs)


def helm_template(*args: str) -> list[dict]:
    """Render the chart (no cluster contact) and return its documents."""
    helm = shutil.which("helm")
    if helm is None:
        import pytest

        pytest.fail("helm is required for the chart guards (CI installs it; locally put it on PATH)")
    out = subprocess.run(
        [helm, "template", "if", str(CHART), "--set", "secrets.failOnPlaceholders=false", *args],
        check=True, capture_output=True, text=True,
    ).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def find(docs: list[dict], kind: str, name_suffix: str) -> dict:
    for d in docs:
        if d.get("kind") == kind and d["metadata"]["name"].endswith(name_suffix):
            return d
    raise AssertionError(f"no {kind} named *{name_suffix} in the render")
