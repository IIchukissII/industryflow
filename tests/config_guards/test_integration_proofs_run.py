# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every real-store proof runs, requires its stack, and blocks a merge (ADR-0026 dec 11).

A proof is a test module marked `integration`. Each side of it fails silently: a proof no step
names never runs; a step that does not set the flag the proof reads turns a missing stack into a
skip, and so does a module that skips itself at import, which no skip-to-failure hook sees; a path
filter that misses the proof skips the job when only the proof changed; a job no gate reads can fail
without blocking; a proof left in the no-stack matrix only skips there, a green that proves nothing.
"""
from __future__ import annotations

import fnmatch
import re
import shlex
import pytest
import yaml

from _repo import ROOT, covered, load_yaml

MARK = re.compile(r"^pytestmark = (?:\[[^\]]*)?pytest\.mark\.integration", re.M)
FLAG = re.compile(r"\bIF_REQUIRE_[A-Z_]+\b")
# A read of the flag, not a mention: os.getenv / os.environ[.get] / a REQUIRE_ENV constant that is read.
READ = re.compile(r"""(?:getenv\(|environ(?:\.get\(|\[))\s*["'](IF_REQUIRE_[A-Z_]+)["']""")
CONST = re.compile(r"""^(\w*REQUIRE\w*)\s*=\s*["'](IF_REQUIRE_[A-Z_]+)["']""", re.M)
WORKFLOWS = sorted(p.relative_to(ROOT).as_posix() for p in (ROOT / ".github" / "workflows").glob("*.yml"))


# The box suite has its own runner and require flag (scripts/box-e2e.sh), outside the workflows.
SCANNED = ("services", "libs", "infrastructure", "scripts", "clients", "extensions", "tests")
EXCLUDED = ("tests/e2e/",)


def _proofs() -> list[str]:
    found = []
    for top in SCANNED:
        for path in sorted((ROOT / top).rglob("test_*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if "node_modules" in path.parts or rel.startswith(EXCLUDED):
                continue
            if MARK.search(path.read_text()):
                found.append(rel)
    return found


def _flags(proof: str) -> set[str]:
    """The require flags the proof reads: in itself, its conftest, and the sibling modules it imports."""
    path = ROOT / proof
    src = path.read_text()
    sources = [src]
    conftest = path.parent / "conftest.py"
    if conftest.exists():
        sources.append(conftest.read_text())
    for name in set(re.findall(r"^\s*(?:from|import) (\w+)", src, re.M)):
        sibling = path.parent / f"{name}.py"
        if sibling.exists():
            sources.append(sibling.read_text())
    flags = set()
    for text in sources:
        flags |= set(READ.findall(text))
        for const, flag in CONST.findall(text):
            if re.search(rf"(?:getenv\(|environ(?:\.get\(|\[))\s*{const}\b", text):
                flags.add(flag)
    return flags


def _set_flags(job: dict, step: dict) -> set[str]:
    env = {**(job.get("env") or {}), **(step.get("env") or {})}
    commands = "\n".join(line for line in step.get("run", "").splitlines() if not line.lstrip().startswith("#"))
    inline = re.findall(r"(IF_REQUIRE_[A-Z_]+)=[\"']?1\b", commands)
    return {k for k, v in env.items() if str(v) == "1"} | set(inline)


def _names(run: str, proof: str) -> bool:
    """Whether a step's command names the proof, by path or by a glob the shell would expand to it."""
    try:
        words = shlex.split(run.replace("\\\n", " "), comments=True)
    except ValueError:
        words = run.split()
    words = [w for chunk in words for w in chunk.split()]
    return any(w == proof or ("*" in w and fnmatch.fnmatchcase(proof, w)) for w in words)


def _steps(proof: str):
    """(workflow, job name, job, step) for every step whose command names the proof."""
    for wf in WORKFLOWS:
        for name, job in (load_yaml(wf).get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if _names(step.get("run", ""), proof):
                    yield wf, name, job, step


def _filter_globs(wf_doc: dict, job: dict) -> list[str]:
    """The path globs of the filter group the job's `if` reads."""
    [output] = re.findall(r"needs\.changes\.outputs\.(\w+)", str(job.get("if", ""))) or [None]
    assert output, f"job gated on `changes` but its if reads no output: {job.get('if')!r}"
    changes = wf_doc["jobs"]["changes"]
    [group] = re.findall(r"steps\.filter\.outputs\.(\w+)", changes["outputs"][output])
    step = next(s for s in changes["steps"] if s.get("id") == "filter")
    return yaml.safe_load(step["with"]["filters"])[group]


def _needs(job: dict) -> list[str]:
    needs = job.get("needs") or []
    return [needs] if isinstance(needs, str) else needs


PROOFS = _proofs()


def test_the_proofs_are_found():
    # A broken marker regex would make every check below pass on nothing.
    assert len(PROOFS) >= 14, PROOFS
    assert "services/tenant_delete/tests/test_cold_store_live.py" in PROOFS


@pytest.mark.parametrize("proof", PROOFS)
def test_the_proof_reads_a_require_flag(proof):
    assert _flags(proof), f"{proof} reads no IF_REQUIRE_* flag: a missing stack can only skip it"


@pytest.mark.parametrize("proof", PROOFS)
def test_the_proof_never_skips_on_a_missing_import(proof):
    # A module skipped at import is a collection skip: no hook that turns a test's skip into a failure
    # sees it, and another module in the same pytest call keeps the exit code green. Its step installs
    # what it imports, so a missing one is an error.
    assert "importorskip" not in (ROOT / proof).read_text(), f"{proof} skips itself on a missing import"


@pytest.mark.parametrize("proof", PROOFS)
def test_a_gated_step_runs_the_proof_with_its_flag_set(proof):
    wanted = _flags(proof)
    runs = list(_steps(proof))
    assert runs, f"no workflow step runs {proof}"
    good = []
    for wf, name, job, step in runs:
        if not wanted & _set_flags(job, step):
            continue
        wf_doc = load_yaml(wf)
        gated = any(name in _needs(j) and "always()" in str(j.get("if", ""))
                    for other, j in wf_doc["jobs"].items() if other != name)
        filtered = "changes" not in _needs(job) or covered(proof, _filter_globs(wf_doc, job))
        if gated and filtered:
            good.append(f"{wf}:{name}")
    assert good, (f"{proof}: every step that runs it either sets none of {sorted(wanted)} to 1, is read by no "
                  f"gate, or sits behind a path filter that misses the proof: {[(w, n) for w, n, _, _ in runs]}")


@pytest.mark.parametrize("proof", PROOFS)
def test_the_no_stack_matrix_leaves_the_proof_out(proof):
    matrix = load_yaml(".github/workflows/unit-tests.yml")["jobs"]["pytest"]["strategy"]["matrix"]["include"]
    for entry in matrix:
        if (proof + "/").startswith(entry["path"].rstrip("/") + "/") or proof == entry["path"]:
            args = shlex.split(entry.get("args", ""))
            ignored = {a.split("=", 1)[1] for a in args if a.startswith("--ignore=")}
            globs = [a.split("=", 1)[1] for a in args if a.startswith("--ignore-glob=")]
            assert proof in ignored or any(fnmatch.fnmatchcase(proof, g) for g in globs), \
                f"{proof} would only skip in the no-stack matrix entry {entry['service']}"
