# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every local module a Spark job imports is in its image and shipped to its executors.

A job image COPYs its script and the job's local modules by name, and names them again in
spark-submit's ``--py-files``. Both are lists whose omission fails silently until the job starts —
or, for --py-files, until an executor first unpickles a function that needs the module. This reads
both sides: the imports of each job script (recursively, from its source) and each Dockerfile's
COPY and --py-files. The Dockerfiles are found by glob, so a new job image is checked too.
"""
import ast
import re
from pathlib import Path

import pytest

JOBS = Path(__file__).resolve().parents[1]
DOCKERFILES = sorted(JOBS.glob("Dockerfile.*"))


def _local_imports(script: Path, seen=None) -> set:
    seen = set() if seen is None else seen
    for node in ast.walk(ast.parse(script.read_text())):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module.split(".")[0]]
        for name in names:
            module = JOBS / f"{name}.py"
            if module.exists() and module.name not in seen:
                seen.add(module.name)
                _local_imports(module, seen)
    return seen


def _job_script(dockerfile: str):
    cmd = re.search(r"^CMD\s*\[(.*?)\]", dockerfile, re.MULTILINE | re.DOTALL)
    scripts = re.findall(r"/app/spark_streaming/(\w+\.py)\"\s*$", cmd.group(1).strip(), re.MULTILINE) if cmd else []
    return scripts[-1] if scripts else None


def _copied(dockerfile: str) -> set:
    files = set()
    for line in re.findall(r"^COPY\s+(.+?)\s+/app/spark_streaming/\s*$", dockerfile, re.MULTILINE):
        files.update(line.split())
    return files


def _py_files(dockerfile: str) -> set:
    m = re.search(r'"--py-files",\s*"([^"]+)"', dockerfile)
    return {Path(p).name for p in m.group(1).split(",")} if m else set()


JOB_IMAGES = [(d, _job_script(d.read_text())) for d in DOCKERFILES if _job_script(d.read_text())]


def test_the_job_images_are_found():
    assert {script for _, script in JOB_IMAGES} == {"kafka_to_timescaledb.py", "kafka_aggregations.py"}


@pytest.mark.parametrize("dockerfile,script", JOB_IMAGES, ids=[d.name for d, _ in JOB_IMAGES])
def test_every_imported_module_is_copied_and_shipped(dockerfile, script):
    text = dockerfile.read_text()
    needed = _local_imports(JOBS / script)
    assert needed, f"{script} imports no local module — the guard would be vacuous"
    copied = _copied(text)
    assert script in copied
    assert needed <= copied, f"{dockerfile.name} does not COPY {sorted(needed - copied)}"
    shipped = _py_files(text)
    assert needed <= shipped, f"{dockerfile.name} --py-files lacks {sorted(needed - shipped)}"
    assert shipped - {CONTRACTS_ZIP} <= copied, f"{dockerfile.name} ships {sorted(shipped - copied)} it never COPYs"


CONTRACTS_ZIP = "industryflow_contracts.zip"


def _imports_contracts(path: Path) -> bool:
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import) and any(a.name.split(".")[0] == "industryflow_contracts" for a in node.names):
            return True
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "industryflow_contracts":
            return True
    return False


@pytest.mark.parametrize("dockerfile,script", JOB_IMAGES, ids=[d.name for d, _ in JOB_IMAGES])
def test_the_shared_contracts_package_is_copied_and_shipped(dockerfile, script):
    """The shared package (libs/industryflow_contracts) comes from the `contracts` build context, not
    from this directory, so the list above cannot see it: it is checked here, on both sides."""
    text = dockerfile.read_text()
    modules = [JOBS / script] + [JOBS / m for m in _local_imports(JOBS / script)]
    needs = any(_imports_contracts(m) for m in modules)
    copies = bool(re.search(r"^COPY\s+--from=contracts\s+industryflow_contracts/\s+/app/spark_streaming/industryflow_contracts/\s*$", text, re.M))
    zipped = f"-m zipfile -c {CONTRACTS_ZIP} industryflow_contracts/" in text
    shipped = CONTRACTS_ZIP in _py_files(text)
    assert needs, f"{script} no longer imports the contracts package — drop it from {dockerfile.name}"
    assert copies and zipped and shipped, (
        f"{dockerfile.name}: the job imports industryflow_contracts, so the image must COPY it from the "
        f"`contracts` context (driver), zip it and name the zip in --py-files (executors); "
        f"copies={copies} zipped={zipped} shipped={shipped}"
    )
