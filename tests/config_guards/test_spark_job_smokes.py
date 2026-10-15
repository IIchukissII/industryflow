# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Each Spark job image's smoke imports the job through the `--py-files` its Dockerfile ships.

The smoke can only see the image, not its CMD, so it restates the job path and the `--py-files` list.
A restated list drifts silently: the smoke would keep passing on a list the job no longer runs with.
"""

from __future__ import annotations

import re

import pytest
from _repo import ROOT, load_yaml

MATRIX = load_yaml(".github/workflows/images.yml")["jobs"]["build-scan"]["strategy"]["matrix"]["include"]
JOBS = [e for e in MATRIX if '"--py-files"' in (ROOT / e["file"]).read_text()]


def _cmd(dockerfile: str) -> tuple[str, str]:
    cmd = re.search(r"^CMD\s*\[(.*?)\]", dockerfile, re.M | re.S).group(1)
    args = re.findall(r'"([^"]*)"', cmd)
    return args[args.index("--py-files") + 1], args[-1]


def test_the_spark_job_images_are_found():
    assert {e["name"] for e in JOBS} >= {"spark-streaming", "spark-aggregations"}


@pytest.mark.parametrize("entry", JOBS, ids=[e["name"] for e in JOBS])
def test_the_smoke_imports_the_job_through_the_cmd_py_files(entry):
    py_files, job = _cmd((ROOT / entry["file"]).read_text())
    smoke = entry.get("smoke", "")
    assert f"spec_from_file_location('job', '{job}')" in smoke, f"{entry['name']}: the smoke does not import {job}"
    assert f"spark-submit --py-files {py_files} " in smoke, (
        f"{entry['name']}: the smoke's --py-files differ from the Dockerfile CMD's {py_files}"
    )
