# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every module an image's code imports from its own build context is copied into the image.

Several Dockerfiles COPY their modules one by one. A module added to the service and imported by one
already copied, but left off that list, passes every unit test (they import from the source tree) and
crashloops the container on its first import — the ingestion image's tenant_status.py did exactly
that. This reads both sides: the imports of what each image copies, and what it copies.
"""
import ast
import re
from pathlib import Path

import pytest
import yaml
from _repo import ROOT


def _compose_builds():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    for name, service in compose["services"].items():
        build = service.get("build")
        if isinstance(build, dict):
            context = (ROOT / build["context"]).resolve()
            yield name, context, (context / build.get("dockerfile", "Dockerfile")).resolve()


BUILDS = list(_compose_builds())


def _copied(context: Path, dockerfile: str) -> set:
    """The .py files an image copies from its own build context (``COPY`` without ``--from``)."""
    files = set()
    for line in re.findall(r"^COPY\s+(?!--from)(.+)$", dockerfile, re.MULTILINE):
        parts = [p for p in line.split() if not p.startswith("--")]
        for src in parts[:-1]:
            target = (context / src).resolve()
            if target.is_dir():
                files.update(p for p in target.rglob("*.py")
                             if "tests" not in p.relative_to(context).parts)
            elif target.suffix == ".py" and target.is_file():
                files.add(target)
    return files


def _local_imports(path: Path, modules: dict) -> set:
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names = [node.module.split(".")[0]]
        found.update(modules[n] for n in names if n in modules)
    return found


def test_the_builds_are_found():
    assert len(BUILDS) >= 10


@pytest.mark.parametrize("name,context,dockerfile", BUILDS, ids=[b[0] for b in BUILDS])
def test_an_image_copies_every_module_its_code_imports(name, context, dockerfile):
    copied = _copied(context, dockerfile.read_text())
    # Top-level modules of the build context, by import name.
    modules = {p.stem: p.resolve() for p in context.glob("*.py")}
    missing = set()
    for path in copied:
        for module in _local_imports(path, modules):
            if module not in copied:
                missing.add(module.relative_to(ROOT).as_posix())
    assert not missing, f"{name}: its copied code imports {sorted(missing)}, which the Dockerfile does not COPY"
