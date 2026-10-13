# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every image whose code imports this package carries it, and every build supplies it.

The package reaches an image through a named build context (``COPY --from=contracts``), not through
the service's own directory. That is three lists whose omission fails silently until a container
starts: the Dockerfiles that copy it, the compose builds and CI builds that supply the ``contracts``
context, and — for the Spark jobs — the ``--py-files`` that ship it to the executors. The unit tests
import the package from the test environment, so a missing COPY leaves them green and the container
crashlooping on its first import. This reads both sides of each list: what an image's copied source
imports, and what its Dockerfile and its builds provide.
"""
import ast
import re
from pathlib import Path

import pytest
import yaml
from _repo import ROOT

REPO = ROOT
CONTEXT_DIR = "libs/industryflow_contracts"
PACKAGE = "industryflow_contracts"
FROM_CONTRACTS = re.compile(r"^COPY\s+--from=contracts\s", re.MULTILINE)


def _imports_package(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == PACKAGE for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == PACKAGE:
            return True
    return False


def _copied_sources(context: Path, dockerfile: str):
    """The .py files an image copies from its own build context (``COPY`` without ``--from``)."""
    sources = set()
    for line in re.findall(r"^COPY\s+(?!--from)(.+)$", dockerfile, re.MULTILINE):
        parts = [p for p in line.split() if not p.startswith("--")]
        for src in parts[:-1]:
            target = (context / src).resolve()
            if target.is_dir():
                sources.update(
                    p for p in target.rglob("*.py")
                    if "tests" not in p.relative_to(context.resolve()).parts
                )
            elif target.suffix == ".py" and target.exists():
                sources.add(target)
    return sources


def _compose_builds():
    compose = yaml.safe_load((REPO / "docker-compose.yml").read_text())
    for name, service in compose["services"].items():
        build = service.get("build")
        if isinstance(build, dict):
            context = REPO / build["context"]
            yield name, context, (context / build.get("dockerfile", "Dockerfile")).resolve(), build


BUILDS = list(_compose_builds())


def _needs(context: Path, dockerfile: Path) -> bool:
    return any(_imports_package(p) for p in _copied_sources(context, dockerfile.read_text()))


def test_the_builds_are_found_and_some_need_the_package():
    assert len(BUILDS) >= 10
    assert sum(_needs(c, d) for _, c, d, _ in BUILDS) >= 10, "the guard would be vacuous"


@pytest.mark.parametrize("name,context,dockerfile,build", BUILDS, ids=[b[0] for b in BUILDS])
def test_an_image_carries_the_package_exactly_when_its_code_imports_it(name, context, dockerfile, build):
    text = dockerfile.read_text()
    needs, has = _needs(context, dockerfile), bool(FROM_CONTRACTS.search(text))
    assert needs == has, (
        f"{dockerfile.relative_to(REPO)}: its code {'imports' if needs else 'does not import'} "
        f"{PACKAGE}, but it {'does not COPY' if needs else 'COPYs'} it --from=contracts"
    )
    if not has:
        return
    if '"--py-files"' in text:
        # A Spark job: the driver imports it from the job's directory, the executors from the zip.
        assert re.search(rf"^COPY\s+--from=contracts\s+{PACKAGE}/\s+/app/spark_streaming/{PACKAGE}/\s*$", text, re.M)
        assert f"python3 -m zipfile -c {PACKAGE}.zip {PACKAGE}/" in text
        py_files = re.search(r'"--py-files",\s*"([^"]+)"', text).group(1).split(",")
        assert f"/app/spark_streaming/{PACKAGE}.zip" in py_files, "the executors would not receive it"
    else:
        assert re.search(r"^COPY\s+--from=contracts\s+\.\s+/tmp/industryflow-contracts\s*$", text, re.M)
        assert re.search(r"^RUN pip install --no-cache-dir /tmp/industryflow-contracts\b", text, re.M), (
            f"{dockerfile.relative_to(REPO)} copies the package but never installs it"
        )


@pytest.mark.parametrize("name,context,dockerfile,build", BUILDS, ids=[b[0] for b in BUILDS])
def test_compose_supplies_the_context_to_every_image_that_takes_it(name, context, dockerfile, build):
    has = bool(FROM_CONTRACTS.search(dockerfile.read_text()))
    supplied = (build.get("additional_contexts") or {}).get("contracts")
    if has:
        assert supplied == f"./{CONTEXT_DIR}", f"compose service {name} builds without the contracts context"
    else:
        assert supplied is None, f"compose service {name} is given a contracts context it does not use"


def test_ci_supplies_the_context_to_every_image_build():
    workflows = sorted((REPO / ".github" / "workflows").glob("*.yml"))
    images = yaml.safe_load((REPO / ".github" / "workflows" / "images.yml").read_text())
    build_step = next(s for s in images["jobs"]["build-scan"]["steps"] if s.get("uses", "").startswith("docker/build-push-action"))
    assert build_step["with"].get("build-contexts") == f"contracts={CONTEXT_DIR}"
    checked = 0
    for wf in workflows:
        for cmd in re.findall(r"docker build\b[^\n]*", wf.read_text()):
            m = re.search(r"-f\s+(\S+)", cmd)
            if m and FROM_CONTRACTS.search((REPO / m.group(1)).read_text()):
                checked += 1
                assert f"--build-context contracts={CONTEXT_DIR}" in cmd, f"{wf.name}: `{cmd}` lacks the contracts context"
    assert checked, "no workflow builds an image that takes the package with `docker build` — update this guard"
