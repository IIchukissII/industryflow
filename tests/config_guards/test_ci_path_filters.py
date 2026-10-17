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
    job = load_yaml(".github/workflows/images.yml")["jobs"]["build-scan"]
    matrix = job["strategy"]["matrix"]["include"]
    # Named build contexts (`COPY --from=<name>`) the build step offers every image, e.g. the shared
    # contracts package: an image that takes one is built from it too.
    build = next(s for s in job["steps"] if s.get("uses", "").startswith("docker/build-push-action"))
    named = dict(line.split("=", 1) for line in (build["with"].get("build-contexts") or "").split())
    assert named, "the build step offers no named context any more — revisit this guard"
    missed = []
    for entry in matrix:
        dockerfile = (ROOT / entry["file"]).read_text()
        extra = [f for name, path in named.items() if f"--from={name} " in dockerfile for f in _files_under(path)]
        for path in [entry["file"], *_files_under(entry["context"]), *extra]:
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
    # the serving image's /app, and the shared contracts package it installs
    "load_in_serving.py": ["services/ml_service/api", "libs/industryflow_contracts"],
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
                    module, package = ROOT / root / f"{top}.py", ROOT / root / top
                    # A package counts whole, with or without an __init__.py (namespace package).
                    files = [module] if module.exists() else \
                        list(package.rglob("*.py")) if package.is_dir() else []
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


# --- compose-smoke.yml ----------------------------------------------------------------------------


def test_compose_smoke_starts_every_edge():
    # The edges proxy by name to services behind them; a wrong upstream starts happily and fails
    # every request, so the smoke must start (and probe) each one.
    services = load_yaml("docker-compose.yml")["services"]
    edges = {
        name for name, svc in services.items()
        if str(svc.get("image", "")).startswith("nginx:")
        or svc.get("build", {}).get("context") == "./services/frontend"
    }
    assert {"frontend", "notebook-sso", "ingestion-edge"} <= edges, edges
    steps = load_yaml(".github/workflows/compose-smoke.yml")["jobs"]["compose-smoke"]["steps"]
    up = next(s["run"] for s in steps if "docker compose" in s.get("run", "") and " up " in s["run"])
    missing = sorted(e for e in edges if e not in up.split())
    assert not missing, f"compose-smoke never starts the edge(s) {missing}"


def test_workflows_that_run_minio_init_watch_its_script():
    script = "infrastructure/minio/minio-init.sh"
    missed = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        doc = load_yaml(f".github/workflows/{wf.name}")
        runs = " ".join(s.get("run", "") for j in doc["jobs"].values() for s in j.get("steps", []))
        if "minio-init" not in runs or "changes" not in doc["jobs"]:
            continue
        if not covered(script, workflow_filter(wf.name)):
            missed.append(wf.name)
    assert not missed, f"workflows that run minio-init but never re-run when it changes: {missed}"


def test_workflows_that_run_the_backup_image_watch_its_build_context():
    # The restore proof runs the db-backup image's scripts; a filter missing them never re-runs it.
    context = _files_under("infrastructure/backup")
    missed = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        doc = load_yaml(f".github/workflows/{wf.name}")
        runs = " ".join(s.get("run", "") for j in doc["jobs"].values() for s in j.get("steps", []))
        if "db-backup" not in runs or "changes" not in doc["jobs"]:
            continue
        globs = workflow_filter(wf.name)
        missed += [f"{wf.name}: {p}" for p in context if not covered(p, globs)]
    assert not missed, "workflows that run the db-backup image but miss its files:\n" + "\n".join(missed)
