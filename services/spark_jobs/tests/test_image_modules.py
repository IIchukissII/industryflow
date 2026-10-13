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
    assert shipped <= copied, f"{dockerfile.name} ships {sorted(shipped - copied)} it never COPYs"
