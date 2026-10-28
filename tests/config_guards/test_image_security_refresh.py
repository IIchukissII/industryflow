# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Every image that takes security fixes at build time re-runs that build daily, and only those do.

Three kinds of layer pick their security fixes up at build: `apt-get upgrade` (the Spark images patch
their pinned Ubuntu), `apk upgrade` (the backup and frontend images patch their Alpine bases) and pip (every Python
image upgrades the base's pip, whose vendored certifi is scanned, and resolves its unpinned transitive
dependencies). The persistent build cache replays
either layer verbatim, so its patch level freezes at the last cache write and the Trivy gate goes red
on a CVE a fresh build would not have — #257/#259 for the OS, GHSA-6q48-6c7w-9gw2 (certifi) for
Python. The daily epoch reaches a layer only when the matrix entry sets `security_refresh` AND the
Dockerfile declares `ARG SECURITY_REFRESH` and uses it at or before that layer. Either side alone
fails silently, so this reads both.
"""
import re

import pytest
from _repo import ROOT, load_yaml

ARG = re.compile(r"^ARG SECURITY_REFRESH\b", re.MULTILINE)
USE = re.compile(r"^RUN echo \"security refresh: \$\{SECURITY_REFRESH\}\"(.*)$", re.MULTILINE)
PIP = re.compile(r"^RUN\b.*\bpip install\b", re.MULTILINE)
APT = re.compile(r"^RUN\b.*\bapt-get upgrade\b", re.MULTILINE)
APK = re.compile(r"^RUN\b.*\bapk upgrade\b", re.MULTILINE)
PIP_UPGRADE = "pip install --no-cache-dir --upgrade pip"

MATRIX = load_yaml(".github/workflows/images.yml")["jobs"]["build-scan"]["strategy"]["matrix"]["include"]


def _dockerfile(entry: dict) -> str:
    # Continuation lines joined, so a RUN reads as one line.
    return (ROOT / entry["file"]).read_text().replace("\\\n", " ")


def test_the_matrix_is_found_and_the_guard_bites():
    assert len(MATRIX) >= 15
    assert sum(bool(e.get("security_refresh")) for e in MATRIX) >= 10, "the guard would be vacuous"


@pytest.mark.parametrize("entry", MATRIX, ids=[e["name"] for e in MATRIX])
def test_an_image_refreshes_exactly_when_a_layer_takes_fixes_at_build(entry):
    text = _dockerfile(entry)
    fixes = [m.start() for rx in (PIP, APT, APK) for m in rx.finditer(text)]
    flagged = bool(entry.get("security_refresh"))
    assert bool(fixes) == flagged, (
        f"{entry['name']}: a layer {'takes' if fixes else 'takes no'} security fixes at build, but the "
        f"matrix {'does not set' if fixes else 'sets'} security_refresh"
    )
    assert bool(ARG.search(text)) == flagged, (
        f"{entry['file']}: the matrix {'passes' if flagged else 'does not pass'} SECURITY_REFRESH, but "
        f"the Dockerfile {'does not declare' if flagged else 'declares'} it"
    )
    if not flagged:
        return
    use = USE.search(text)
    assert use and use.start() <= min(fixes), (
        f"{entry['file']}: SECURITY_REFRESH must be used at or before the first layer that takes fixes, "
        "or that layer keeps its cache key"
    )
    if PIP.search(text):
        assert PIP_UPGRADE in use.group(1), (
            f"{entry['file']}: the refreshed layer must upgrade pip, or the base's pip and its vendored "
            "certifi stay as old as the base image"
        )


def test_the_epoch_reaches_the_build_only_for_flagged_images():
    steps = load_yaml(".github/workflows/images.yml")["jobs"]["build-scan"]["steps"]
    epoch = next(s for s in steps if s.get("id") == "epoch")
    build = next(s for s in steps if s.get("uses", "").startswith("docker/build-push-action"))
    assert epoch["if"] == "matrix.security_refresh"
    assert "matrix.security_refresh" in build["with"]["build-args"]
    assert "SECURITY_REFRESH=" in build["with"]["build-args"]
