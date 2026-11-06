# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every one-shot a workflow or script starts with `docker compose up` has its exit code read
(ADR-0026 dec 6, 11).

An attached `docker compose up <one-shot>` exits 0 when the one-shot fails, and `docker compose wait`
does not see a container that has already exited, so a failed bucket or schema step leaves the proof
after it running on a half-made store. Compose reads a one-shot's code in two cases of `up` only:
`--exit-code-from <it>`, and a dependent started by the same `up` (without `--no-deps`) that waits on
it with `service_completed_successfully`. A detached or `--wait` start also passes when a later line
of the same job or script reads the one-shot's `ExitCode`. `run --rm` returns the container's code and
is not policed here.

The one-shots come from the compose files the command names: every service another waits on with
`service_completed_successfully`, and every `restart: "no"` service except a build-only image whose
command is `true`.
"""

from __future__ import annotations

import ast
import re
import shlex

import pytest
import yaml

from _repo import ROOT

DEFAULT_FILE = "docker-compose.yml"
COMPLETED = "service_completed_successfully"
# Compose's global options that take a value, and `up`'s.
GLOBAL_VALUE = {"-f", "--file", "-p", "--project-name", "--project-directory", "--profile", "--env-file",
                "--ansi", "--progress", "--parallel"}
UP_VALUE = {"--exit-code-from", "--wait-timeout", "--scale", "-t", "--timeout", "--pull", "--attach",
            "--no-attach"}
SEPARATORS = {"&&", "||", ";", ";;", "|", "&", "(", ")", "()", "{", "}", "`"}
HEREDOC = re.compile(r"<<-?\s*['\"]?(\w+)['\"]?")
ARRAY = re.compile(r"^\s*([A-Za-z_]\w*)(\+?)=\((.*)\)\s*$")
ARRAY_REF = re.compile(r"^\$\{([A-Za-z_]\w*)\[@\]\}$")

Command = tuple[list[str], str]  # the tokens after `docker compose`, and the text from its line on


# --- the compose file -----------------------------------------------------------------------------

def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(out.get(k), dict) and isinstance(v, dict) else v
    return out


def services(files: tuple[str, ...] = (DEFAULT_FILE,)) -> dict:
    """The services of the named compose files, merged; a file outside the repository (another
    checkout's) reads as the repository's own."""
    merged: dict = {}
    for f in files:
        path = ROOT / f
        doc = yaml.safe_load((path if path.is_file() else ROOT / DEFAULT_FILE).read_text())
        merged = _merge(merged, doc.get("services") or {})
    return merged


def _depends(svc: dict) -> dict:
    deps = svc.get("depends_on") or {}
    return {d: {} for d in deps} if isinstance(deps, list) else deps


def _waited(svcs: dict) -> set[str]:
    return {d for s in svcs.values() for d, c in _depends(s).items() if (c or {}).get("condition") == COMPLETED}


def one_shots(svcs: dict) -> set[str]:
    no_restart = {n for n, s in svcs.items() if str(s.get("restart", "")) == "no" and s.get("command") != ["true"]}
    return _waited(svcs) | no_restart


# --- invocations ----------------------------------------------------------------------------------

def _logical_lines(text: str) -> list[str]:
    """Shell lines with continuations joined, comment lines and heredoc bodies dropped."""
    out, buf, end = [], "", None
    for line in text.splitlines():
        if end is not None:
            if line.strip() == end:
                end = None
            continue
        if line.rstrip().endswith("\\"):
            buf += line.rstrip()[:-1] + " "
            continue
        line, buf = buf + line, ""
        if line.lstrip().startswith("#"):
            continue
        if m := HEREDOC.search(line):
            end = m.group(1)
        out.append(line)
    return out


def _tokens(line: str) -> list[str]:
    lex = shlex.shlex(line, posix=True, punctuation_chars=";&|()")
    lex.whitespace_split = True
    lex.commenters = "#"
    try:
        return list(lex)
    except ValueError:
        return line.split()


def shell_commands(text: str) -> list[Command]:
    """Every `docker compose …` command in a shell text. `NAME=(…)` arrays defined in the text are
    expanded where `"${NAME[@]}"` is used, and a function `f() { docker compose … "$@"; }` where `f`
    is called."""
    arrays: dict[str, list[str]] = {}
    wrappers: dict[str, list[str]] = {}
    cmds = []
    lines = _logical_lines(text)
    for n, line in enumerate(lines):
        if m := ARRAY.match(line):
            arrays[m.group(1)] = (arrays.get(m.group(1), []) if m.group(2) else []) + _tokens(m.group(3))
            continue
        toks = []
        for t in _tokens(line):
            ref = ARRAY_REF.match(t)
            toks.extend(arrays.get(ref.group(1), [t]) if ref else [t])
        found = []
        for i in range(len(toks)):
            if toks[i].endswith("docker") and toks[i + 1:i + 2] == ["compose"]:
                prefix, start = [], i + 2
            elif toks[i] in wrappers and toks[i + 1:i + 2] != ["()"]:
                prefix, start = wrappers[toks[i]], i + 1
            else:
                continue
            rest = []
            for t in toks[start:]:
                if t in SEPARATORS:
                    break
                rest.append(t)
            found.append(prefix + rest)
        if toks[1:3] == ["()", "{"] and found and found[0][-1:] == ["$@"]:
            wrappers[toks[0]] = found[0][:-1]
            continue
        cmds.extend((f, "\n".join(lines[n:])) for f in found)
    return cmds


def python_commands(text: str) -> list[Command]:
    """Every `"docker", "compose", …` argument sequence in a Python source (call arguments or a list),
    and every shell command quoted in its strings."""
    cmds, quoted = [], []
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and "docker compose" in node.value:
            quoted.append(node.value)
        items = node.args if isinstance(node, ast.Call) else node.elts if isinstance(node, (ast.List, ast.Tuple)) else []
        vals = [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else "$expr" for a in items]
        for i in range(len(vals) - 1):
            if vals[i] == "docker" and vals[i + 1] == "compose":
                cmds.append((vals[i + 2:], ""))
    return cmds + shell_commands("\n".join(quoted))


def _parse(cmd: list[str]) -> dict | None:
    """A compose command's files, profiles, subcommand and `up` options; None without a subcommand."""
    files, profiles, i = [], [], 0
    while i < len(cmd) and cmd[i].startswith("-"):
        opt, _, val = cmd[i].partition("=")
        if opt in GLOBAL_VALUE:
            if not val:
                i += 1
                val = cmd[i] if i < len(cmd) else ""
            if opt in {"-f", "--file"}:
                files.append(val)
            elif opt == "--profile":
                profiles.append(val)
        i += 1
    if i >= len(cmd):
        return None
    sub, args = cmd[i], cmd[i + 1:]
    up = {"targets": [], "exit_code_from": None, "detach": False, "wait": False, "no_deps": False}
    j = 0
    while j < len(args):
        opt, _, val = args[j].partition("=")
        if opt in UP_VALUE:
            if not val:
                j += 1
                val = args[j] if j < len(args) else ""
            if opt == "--exit-code-from":
                up["exit_code_from"] = val
        elif opt in {"-d", "--detach"}:
            up["detach"] = True
        elif opt == "--wait":
            up["wait"] = True
        elif opt == "--no-deps":
            up["no_deps"] = True
        elif not opt.startswith("-"):
            up["targets"].append(opt)
        j += 1
    return {"files": tuple(files) or (DEFAULT_FILE,), "profiles": set(profiles), "sub": sub, **up}


def _started(svcs: dict, c: dict) -> set[str] | None:
    """The services an `up` starts, or None when a target is not a literal name."""
    if any(t.startswith("$") for t in c["targets"]):
        return None
    if not c["targets"]:
        return {n for n, s in svcs.items() if not s.get("profiles") or set(s["profiles"]) & c["profiles"]}
    started, todo = set(), list(c["targets"])
    while todo:
        n = todo.pop()
        if n in started or n not in svcs:
            continue
        started.add(n)
        if not c["no_deps"]:
            todo.extend(_depends(svcs[n]))
    return started


def violations(unit: str, cmds: list[Command]) -> list[str]:
    """The one-shot starts in one job or script whose failure would pass."""
    bad = []
    for toks, after in cmds:
        c = _parse(toks)
        if not c or c["sub"] != "up":
            continue
        svcs = services(c["files"])
        started = _started(svcs, c)
        if started is None:
            continue
        for x in sorted(one_shots(svcs) & started):
            gated = not c["no_deps"] and any(
                (_depends(svcs[y]).get(x) or {}).get("condition") == COMPLETED for y in started)
            if gated or c["exit_code_from"] == x:
                continue
            if (c["detach"] or c["wait"]) and any(x in t and "ExitCode" in t for t in after.splitlines()):
                continue
            bad.append(f"{unit}: docker compose {' '.join(toks)}  -> {x}'s exit code is never read")
    return bad


def all_units() -> list[tuple[str, list[Command]]]:
    units = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        for name, job in (yaml.safe_load(wf.read_text()).get("jobs") or {}).items():
            # One text per job, so an exit check in a later step counts.
            text = "\n".join(s.get("run", "") for s in job.get("steps", []))
            units.append((f"{wf.relative_to(ROOT)}:{name}", shell_commands(text)))
    e2e = ROOT / "tests" / "e2e"
    for p in [*sorted((ROOT / "scripts").rglob("*.sh")), *sorted(e2e.glob("Dockerfile*"))]:
        units.append((str(p.relative_to(ROOT)), shell_commands(p.read_text())))
    for p in [*sorted((ROOT / "scripts").rglob("*.py")), *sorted(e2e.rglob("*.py"))]:
        units.append((str(p.relative_to(ROOT)), python_commands(p.read_text())))
    return units


# --- the guard ------------------------------------------------------------------------------------

def test_the_one_shot_set_comes_from_the_compose_file():
    svcs = services()
    # Every service another waits on to complete is a restart-"no" service: the two readings agree.
    assert _waited(svcs) and _waited(svcs) <= {n for n, s in svcs.items() if str(s.get("restart")) == "no"}
    assert {"minio-init", "db-migrate"} <= one_shots(svcs)


def test_the_guard_reads_the_invocations():
    units = dict(all_units())
    ups = [c for cmds in units.values() for c in (_parse(t) for t, _ in cmds) if c and c["sub"] == "up"]
    assert len(ups) >= 15, len(ups)
    # The box deploy's schema step is seen, and passes by its exit code.
    deploy = [t for t, _ in units["scripts/box-deploy.sh"]]
    assert ["up", "--no-deps", "--exit-code-from", "db-migrate", "db-migrate"] in deploy, deploy
    assert any(t[:2] == ["--profile", "notebooks"] and t[-2:] == ["up", "-d"] for t in deploy), deploy
    assert [t for t, _ in units["tests/e2e/harness.py"]], "the e2e harness's compose calls are not read"


def test_every_one_shot_start_reads_its_exit_code():
    bad = [v for unit, cmds in all_units() for v in violations(unit, cmds)]
    assert not bad, "one-shots started so that their failure passes:\n" + "\n".join(bad)


# The guard's own sight: a parser change that blinds it fails here.
CAUGHT = [
    "docker compose up minio-init",
    "docker compose up -d --wait minio\n  docker compose up minio-init",
    "docker compose -f docker-compose.yml -f docker-compose.ci-notebooks.yml \\\n  --profile notebooks up minio-init",
    "docker compose up -d --wait db-migrate",
    "docker compose up --exit-code-from minio minio-init",
    "docker compose up --no-deps -d minio minio-init",
    "docker compose --profile backup up -d",
    "set -e; docker compose up db-migrate && echo ok",
    "P=(--profile backup)\ndocker compose \"${P[@]}\" up -d",
    'prev() { docker compose -p x --project-directory "$T/prev" -f "$T/prev/docker-compose.yml" "$@"; }\n'
    "prev up -d --wait timescaledb\nprev up minio-init",
]
PASSED = [
    "docker compose run --rm --no-deps minio-init",
    "docker compose up --no-deps --exit-code-from db-migrate db-migrate",
    "docker compose up --exit-code-from=minio-init minio-init",
    "docker compose up -d --wait timescaledb minio redis",
    "docker compose --profile notebooks up -d",
    "docker compose up -d db-migrate\ndocker inspect -f '{{.State.ExitCode}}' $(docker compose ps -aq db-migrate)",
    "python3 - <<'PY'\ndocker compose up minio-init\nPY",
    "# docker compose up minio-init",
]


@pytest.mark.parametrize("text", CAUGHT)
def test_the_guard_catches(text):
    assert violations("t", shell_commands(text)), text


@pytest.mark.parametrize("text", PASSED)
def test_the_guard_passes(text):
    assert not violations("t", shell_commands(text))


def test_the_guard_reads_python_argument_lists():
    assert violations("t", python_commands('subprocess.run(["docker", "compose", "--profile", p, "up", "minio-init"])'))
    assert violations("t", python_commands('"""Usage: docker compose up minio-init"""'))


def test_the_guard_reads_a_job_across_its_steps():
    wf = yaml.safe_load("jobs:\n  j:\n    steps:\n      - run: docker compose up -d --wait minio\n"
                        "      - run: |\n          docker compose \\\n            up minio-init\n")
    assert violations("t", shell_commands("\n".join(s["run"] for s in wf["jobs"]["j"]["steps"])))
