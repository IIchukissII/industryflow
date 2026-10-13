# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The digest overlay pins every first-party image the chart deploys, and only images CI publishes.

The overlay used to be generated from a hand list beside the chart: it still named a retired image
and missed seven live ones, which then shipped on the mutable tag in a "digest-pinned" install.
"""

from __future__ import annotations

import sys

import yaml
from _repo import ROOT, helm_template, load_yaml

sys.path.insert(0, str(ROOT / "scripts"))

from pin_image_digests import first_party_images, render_overlay  # noqa: E402

VALUES = load_yaml("deploy/helm/industryflow/values.yaml")

# Published by images.yml but deliberately not deployed by the chart: Helm runs Spark in local mode,
# so the standalone worker image has no workload here.
NOT_DEPLOYED_BY_CHART = {"industryflow-spark-worker"}


def _published() -> set[str]:
    matrix = load_yaml(".github/workflows/images.yml")["jobs"]["build-scan"]["strategy"]["matrix"]["include"]
    return {f"industryflow-{e['name']}" for e in matrix}


def test_every_first_party_image_the_chart_deploys_is_found():
    images = set(first_party_images(VALUES).values())
    # Read from values.yaml independently of the script: every first-party `image:` value.
    expected = set()

    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "image" and isinstance(v, str) and v.startswith("industryflow-"):
                    expected.add(v)
                else:
                    walk(v)

    walk(VALUES)
    assert images == expected, f"missed: {sorted(expected - images)}; extra: {sorted(images - expected)}"


def test_the_chart_and_the_image_pipeline_name_the_same_images():
    deployed = set(first_party_images(VALUES).values())
    published = _published()
    assert not deployed - published, f"the chart deploys images CI never publishes: {sorted(deployed - published)}"
    unexpected = published - deployed - NOT_DEPLOYED_BY_CHART
    assert not unexpected, f"published but neither deployed nor listed as not deployed: {sorted(unexpected)}"


def test_the_overlay_lands_every_digest_in_the_rendered_chart(tmp_path):
    images = first_party_images(VALUES)
    fake = {path: "sha256:" + f"{i:064x}" for i, path in enumerate(images)}
    overlay = tmp_path / "values-digests.yaml"
    overlay.write_text(render_overlay(fake, "latest", "ghcr.io/example"))
    assert "alert-service-api" not in overlay.read_text()
    rendered = yaml.safe_dump_all(helm_template(
        "-f", str(overlay), "--set", "notebookHub.enabled=true", "--set", "backup.enabled=true",
        "--set", "coldExport.enabled=true",
    ))
    missing = [".".join(p) for p, d in fake.items() if d not in rendered]
    assert not missing, f"overlay keys that pin nothing in the render: {missing}"
