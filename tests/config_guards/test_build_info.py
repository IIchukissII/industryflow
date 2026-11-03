# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every platform service that serves metrics reports the revision its image was built from
(ADR-0016 dec 8). Each link is a place an omission fails silently: a service that never calls the
report, a Dockerfile that drops the build argument, a build (compose, CI, the box deploy) that never
passes it. The series then reads `unknown`, or is missing, and a deploy leaves no trace."""
from __future__ import annotations

import re
from pathlib import Path

from _repo import ROOT, load_yaml

METRICS_SERVICES = sorted(
    p.parent for p in ROOT.glob("services/**/main.py")
    if "node_modules" not in p.parts and "Instrumentator" in p.read_text()
)
COMPOSE = load_yaml("docker-compose.yml")["services"]
REPORT = re.compile(r'^build_info\.report\("([a-z0-9-]+)"\)$', re.M)


def _compose_services(service_dir: Path) -> dict[str, dict]:
    """Every compose service built from this directory's image (a one-shot may share it)."""
    found = {}
    for name, svc in COMPOSE.items():
        build = svc.get("build")
        if not isinstance(build, dict):
            continue
        dockerfile = (ROOT / build["context"] / build.get("dockerfile", "Dockerfile")).resolve()
        if dockerfile == (service_dir / "Dockerfile").resolve():
            found[name] = build
    assert found, f"{service_dir.relative_to(ROOT)}: no compose service builds it"
    return found


def test_metrics_services_are_found():
    assert len(METRICS_SERVICES) >= 3, METRICS_SERVICES


def test_every_metrics_service_reports_its_revision_under_its_service_name():
    for d in METRICS_SERVICES:
        names = _compose_services(d)
        reported = REPORT.findall((d / "main.py").read_text())
        assert len(reported) == 1 and reported[0] in names, \
            f"{d.relative_to(ROOT)}/main.py: reports {reported}, expected one of {sorted(names)}"


def test_every_metrics_image_keeps_the_revision_it_was_built_with():
    for d in METRICS_SERVICES:
        df = (d / "Dockerfile").read_text()
        rel = d.relative_to(ROOT)
        assert re.search(r"^ARG INDUSTRYFLOW_REVISION=unknown$", df, re.M), f"{rel}: no ARG INDUSTRYFLOW_REVISION"
        assert re.search(r"^ENV INDUSTRYFLOW_REVISION=\$\{INDUSTRYFLOW_REVISION\}$", df, re.M), \
            f"{rel}: the build argument is not kept in the environment"


def test_every_build_passes_the_commit():
    for d in METRICS_SERVICES:
        for name, build in _compose_services(d).items():
            assert (build.get("args") or {}).get("INDUSTRYFLOW_REVISION") == "${INDUSTRYFLOW_REVISION:-unknown}", \
                f"compose {name}: the build does not pass INDUSTRYFLOW_REVISION"
    images = (ROOT / ".github/workflows/images.yml").read_text()
    assert re.search(r"^\s+INDUSTRYFLOW_REVISION=\$\{\{ github\.sha \}\}$", images, re.M), \
        "images.yml: the CI build does not pass the commit"
    deploy = (ROOT / "scripts/box-deploy.sh").read_text()
    build_at = deploy.index('docker compose "${PROFILES[@]}" build')
    assert 'export INDUSTRYFLOW_REVISION="$sha"' in deploy[:build_at], \
        "box-deploy.sh: the commit is not exported before the build"
