# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the hub / spawn-image JupyterHub parity check.

The first failing case is the real diff that prompted it: a bot bumped the hub's requirements.txt
from jupyterhub 5.5.0 to 6.0.1 and left both spawn images' Dockerfile pins at 5.5.0.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from check_jupyterhub_parity import find_mismatches  # noqa: E402

DOCKERFILE = "RUN pip install --no-cache-dir \\\n    jupyterhub=={v} \\\n    jupyterlab==4.5.0\n"


def make(tmp_path: Path, hub: str, images: dict[str, str | None]) -> Path:
    d = tmp_path / "services/notebook_hub"
    d.mkdir(parents=True)
    (d / "requirements.txt").write_text(f"jupyterhub=={hub}\njupyterhub-kubespawner==7.0.0\n")
    for name, v in images.items():
        (d / f"Dockerfile.{name}").write_text(DOCKERFILE.format(v=v) if v else "FROM python:3.14\n")
    return tmp_path


def test_hub_bumped_images_left_behind_fails(tmp_path):
    root = make(tmp_path, "6.0.1", {"authoring": "5.5.0", "analytics": "5.5.0"})
    assert len(find_mismatches(root)) == 2


def test_all_agree_passes(tmp_path):
    assert find_mismatches(make(tmp_path, "6.0.1", {"authoring": "6.0.1", "analytics": "6.0.1"})) == []


def test_image_without_pin_fails(tmp_path):
    assert find_mismatches(make(tmp_path, "6.0.1", {"authoring": "6.0.1", "analytics": None}))


def test_sibling_packages_are_not_mistaken_for_the_pin(tmp_path):
    # jupyterhub-kubespawner==7.0.0 in the hub file must not count as a second jupyterhub pin.
    assert find_mismatches(make(tmp_path, "5.5.0", {"authoring": "5.5.0"})) == []


def test_repo_is_consistent():
    assert find_mismatches() == []
