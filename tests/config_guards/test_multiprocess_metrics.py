# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A uvicorn service that serves Prometheus metrics aggregates them across its workers.

Without PROMETHEUS_MULTIPROC_DIR each worker keeps its own counters and a scrape reads whichever
worker answers, so request rates and latencies on the dashboards and alert rules are wrong whenever
a service runs more than one worker. The directory is emptied at start, and compose must not
replace the image's command (an override once pinned `--workers 2` past the setup).
"""

from __future__ import annotations

import re

from _repo import ROOT, load_yaml

METRICS_SERVICES = sorted(
    p.parent for p in ROOT.glob("services/**/main.py")
    if "node_modules" not in p.parts and "Instrumentator" in p.read_text()
)
COMPOSE = load_yaml("docker-compose.yml")


def _dockerfile(service_dir):
    return (service_dir / "Dockerfile").read_text()


def test_metrics_services_are_found():
    assert len(METRICS_SERVICES) >= 3, METRICS_SERVICES


def test_every_metrics_service_aggregates_its_workers():
    for d in METRICS_SERVICES:
        df = _dockerfile(d)
        rel = d.relative_to(ROOT)
        assert re.search(r"^ENV PROMETHEUS_MULTIPROC_DIR=/tmp/prometheus-multiproc$", df, re.M), f"{rel}: no PROMETHEUS_MULTIPROC_DIR"
        assert "mkdir -p /tmp/prometheus-multiproc" in df, f"{rel}: the image must create the directory (imports outside CMD)"
        cmd = next(line for line in df.splitlines() if line.startswith("CMD "))
        assert 'rm -rf \\"$PROMETHEUS_MULTIPROC_DIR\\"' in cmd and 'mkdir -p \\"$PROMETHEUS_MULTIPROC_DIR\\"' in cmd, (
            f"{rel}: CMD must empty and create the directory before uvicorn starts")
        assert "exec uvicorn" in cmd, f"{rel}: uvicorn must be the container's main process"


def test_compose_keeps_the_image_command():
    # Every service that serves from the image; a one-shot that runs another program from it (the
    # dashboards' key, ADR-0016 dec 7) serves no metrics and starts no server.
    builds = {s: c for s, c in COMPOSE["services"].items()
              if isinstance(c.get("build"), dict) and c["build"].get("dockerfile")
              and c.get("restart") != "no"}
    assert len(builds) >= 5, builds
    for d in METRICS_SERVICES:
        dockerfile = str((d / "Dockerfile").relative_to(ROOT))
        for name, svc in builds.items():
            path = str((ROOT / svc["build"].get("context", ".") / svc["build"]["dockerfile"]).resolve().relative_to(ROOT))
            if path == dockerfile:
                assert "command" not in svc, f"compose service {name} overrides {dockerfile}'s CMD"
