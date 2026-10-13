# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Third-party images: pinned by digest in both deployments, and the same pin in both.

The chart pins by digest (ADR-0009); the compose file used to run `:latest` for half its images, so
the box ran whatever the registry served that day and the two deployments drifted apart unobserved.
"""

from __future__ import annotations

import re

from _repo import load_yaml

DIGEST = re.compile(r"^[^@\s]+:[^@\s]+@sha256:[0-9a-f]{64}$")


def _repo(ref: str) -> str:
    name = ref.split("@")[0].rsplit(":", 1)[0]
    name = name.removeprefix("docker.io/")
    return name.removeprefix("library/")


def _compose_images() -> dict[str, str]:
    services = load_yaml("docker-compose.yml")["services"]
    # A service that is built here carries its own `image:` name for the build result, not a pull.
    return {n: s["image"] for n, s in services.items() if "image" in s and "build" not in s}


def _helm_images() -> dict[str, str]:
    found: dict[str, str] = {}

    def walk(node, path):
        if isinstance(node, dict):
            for k, v in node.items():
                if isinstance(v, str) and k in {"image", "initImage"} and not v.startswith("industryflow-"):
                    found[".".join(path + [k])] = v
                else:
                    walk(v, path + [k])

    walk(load_yaml("deploy/helm/industryflow/values.yaml"), [])
    return found


def test_every_compose_pull_image_is_pinned_by_digest():
    loose = {n: ref for n, ref in _compose_images().items() if not DIGEST.match(ref)}
    assert not loose, f"compose images without tag@sha256 pin: {loose}"


def test_every_chart_third_party_image_is_pinned_by_digest():
    images = _helm_images()
    assert len(images) >= 8, f"found only {sorted(images)} — the guard is not reading the values"
    loose = {p: ref for p, ref in images.items() if not DIGEST.match(ref)}
    assert not loose, f"values.yaml images without tag@sha256 pin: {loose}"


def test_compose_and_chart_pin_the_same_image_identically():
    helm_by_repo: dict[str, set[str]] = {}
    for ref in _helm_images().values():
        helm_by_repo.setdefault(_repo(ref), set()).add(ref)
    shared, drift = 0, []
    for name, ref in _compose_images().items():
        if _repo(ref) in helm_by_repo:
            shared += 1
            if ref not in helm_by_repo[_repo(ref)]:
                drift.append(f"{name}: compose {ref} vs chart {sorted(helm_by_repo[_repo(ref)])}")
    assert shared >= 8, f"only {shared} images shared — the guard is not matching repositories"
    assert not drift, "compose and the chart pin different builds:\n" + "\n".join(drift)


# spark-master's version against the job images is scripts/check_spark_version_parity.py's job.


def test_dependabot_watches_the_root_compose_file():
    updates = load_yaml(".github/dependabot.yml")["updates"]
    compose = [u for u in updates if u["package-ecosystem"] == "docker-compose"]
    assert compose and any(u.get("directory") == "/" or "/" in u.get("directories", []) for u in compose)
