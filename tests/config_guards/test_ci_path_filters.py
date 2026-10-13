# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A path filter is a list whose omission fails silently: the gate simply never runs.

Each test reads the filter AND the thing the gate actually consumes (build contexts, imported
modules, files the chart reads through symlinks) and fails on any consumed path the filter misses.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

from _repo import CHART, ROOT, covered, load_yaml, workflow_filter


def _rel(p: Path) -> str:
    return p.resolve().relative_to(ROOT).as_posix()


def _files_under(rel_dir: str) -> list[str]:
    base = ROOT / rel_dir
    return [_rel(p) for p in base.rglob("*") if p.is_file() and "node_modules" not in p.parts]


# --- images.yml -----------------------------------------------------------------------------------


def test_every_image_build_context_is_in_the_pr_filter():
    globs = workflow_filter("images.yml")
    matrix = load_yaml(".github/workflows/images.yml")["jobs"]["build-scan"]["strategy"]["matrix"]["include"]
    missed = []
    for entry in matrix:
        for path in [entry["file"], *_files_under(entry["context"])]:
            if not covered(path, globs):
                missed.append(f"{entry['name']}: {path}")
    assert not missed, "images.yml PR filter misses files an image is built from:\n" + "\n".join(missed)


def test_a_push_to_main_publishes_regardless_of_the_filter():
    # #314/#315 changed only .py files; the filter skipped them and main never published.
    relevant = load_yaml(".github/workflows/images.yml")["jobs"]["changes"]["outputs"]["relevant"]
    assert "github.event_name != 'pull_request'" in relevant, relevant
    build = load_yaml(".github/workflows/images.yml")["jobs"]["build-scan"]
    assert "changes" in build["needs"] and "needs.changes.outputs.relevant" in build["if"]


# --- model-artifact-roundtrip.yml -----------------------------------------------------------------

# Where each harness script runs, i.e. which source tree its first-party imports resolve against.
_HARNESS_PATHS = {
    "load_in_serving.py": ["services/ml_service/api"],   # the serving image's /app
    "train_in_kernel.py": ["clients/python"],            # the authoring image installs the client
}


def _first_party_closure(script: Path, roots: list[str]) -> set[str]:
    stdlib = set(sys.stdlib_module_names)
    seen: set[str] = set()
    todo = [script]
    while todo:
        tree = ast.parse(todo.pop().read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module]
            for name in names:
                top = name.split(".")[0]
                if top in stdlib:
                    continue
                for root in roots:
                    for cand in (ROOT / root / f"{top}.py", ROOT / root / top / "__init__.py"):
                        if cand.exists():
                            files = [cand] if cand.suffix == ".py" and cand.name != "__init__.py" \
                                else list(cand.parent.rglob("*.py"))
                            for f in files:
                                rel = _rel(f)
                                if rel not in seen:
                                    seen.add(rel)
                                    todo.append(f)
    return seen


def test_roundtrip_filter_covers_every_module_the_harness_imports():
    globs = workflow_filter("model-artifact-roundtrip.yml")
    harness = ROOT / "tests" / "model_artifact_roundtrip"
    missed = []
    for script, roots in _HARNESS_PATHS.items():
        modules = _first_party_closure(harness / script, roots)
        assert modules, f"{script}: resolved no first-party imports — the guard is not looking"
        missed += [f"{script} -> {m}" for m in sorted(modules) if not covered(m, globs)]
    assert not missed, "model-artifact-roundtrip filter misses imported modules:\n" + "\n".join(missed)


def test_roundtrip_harness_scripts_are_all_mapped():
    scripts = {p.name for p in (ROOT / "tests" / "model_artifact_roundtrip").glob("*.py")}
    assert scripts == set(_HARNESS_PATHS), "a harness script was added; map where it runs"


# --- helm.yml -------------------------------------------------------------------------------------


def test_helm_filter_covers_what_the_chart_reads_through_symlinks():
    globs = workflow_filter("helm.yml")
    links = [p for p in CHART.rglob("*") if p.is_symlink()]
    assert links, "the chart reads nothing through symlinks any more — revisit this guard"
    missed = []
    for link in links:
        target = link.resolve()
        files = [target] if target.is_file() else [p for p in target.rglob("*") if p.is_file()]
        missed += [f"{link.relative_to(ROOT)} -> {_rel(f)}"
                   for f in files if not covered(_rel(f), globs)]
    assert not missed, "helm.yml filter misses files the chart renders:\n" + "\n".join(missed)
