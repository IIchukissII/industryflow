# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every image the platform builds that runs platform code and serves metrics reports the revision it
was built from (ADR-0016 dec 8): the Python web services through their own /metrics, the Spark drivers
on a port of their own. Each link is a place an omission fails silently: a process that never calls the
report, a Dockerfile that drops the build argument, a build (compose, CI, the box deploy) that never
passes it, a scrape (compose Prometheus, the chart) that never reads the port. The series then reads
`unknown`, or is missing, and a deploy leaves no trace."""
from __future__ import annotations

import re
from pathlib import Path

from _repo import ROOT, load_yaml

from industryflow_contracts.build_info import DEFAULT_SERVE_PORT

COMPOSE = load_yaml("docker-compose.yml")["services"]
REPORT = re.compile(r'^build_info\.report\("([a-z0-9-]+)"\)$', re.M)
SERVE = re.compile(r'^\s*build_info\.serve\("([a-z0-9-]+)"\)$', re.M)
SPARK = ROOT / "services/spark_jobs"


def _web_service(main: Path) -> dict:
    return {"entry": main, "dockerfile": main.parent / "Dockerfile", "call": REPORT}


def _spark_driver(dockerfile: Path) -> dict:
    job = re.findall(r'"/app/spark_streaming/(\w+\.py)"\]', dockerfile.read_text())
    return {"entry": SPARK / job[-1], "dockerfile": dockerfile, "call": SERVE}


# The web services serve /metrics through the instrumentator; each Spark job image runs a Python driver
# (its CMD ends in a job script). The worker image runs no platform code; the master is the stock image.
REPORTERS = sorted(
    [_web_service(p) for p in ROOT.glob("services/**/main.py")
     if "node_modules" not in p.parts and "Instrumentator" in p.read_text()]
    + [_spark_driver(d) for d in SPARK.glob("Dockerfile.*") if '"--py-files"' in d.read_text()],
    key=lambda r: str(r["entry"]),
)


def _compose_services(dockerfile: Path) -> dict[str, dict]:
    """Every compose service built from this Dockerfile (a one-shot may share it)."""
    found = {}
    for name, svc in COMPOSE.items():
        build = svc.get("build")
        if not isinstance(build, dict):
            continue
        built = (ROOT / build["context"] / build.get("dockerfile", "Dockerfile")).resolve()
        if built == dockerfile.resolve():
            found[name] = build
    assert found, f"{dockerfile.relative_to(ROOT)}: no compose service builds it"
    return found


def _component(r: dict) -> str:
    return r["call"].findall(r["entry"].read_text())[0]


def test_reporters_are_found():
    assert sum(r["call"] is REPORT for r in REPORTERS) >= 3, REPORTERS
    assert {_component(r) for r in REPORTERS if r["call"] is SERVE} == {"spark-streaming", "spark-aggregations"}


def test_every_reporter_reports_its_revision_under_its_service_name():
    for r in REPORTERS:
        names = _compose_services(r["dockerfile"])
        reported = r["call"].findall(r["entry"].read_text())
        assert len(reported) == 1 and reported[0] in names, \
            f"{r['entry'].relative_to(ROOT)}: reports {reported}, expected one of {sorted(names)}"


def test_every_reporter_image_keeps_the_revision_it_was_built_with():
    for r in REPORTERS:
        df = r["dockerfile"].read_text()
        rel = r["dockerfile"].relative_to(ROOT)
        assert re.search(r"^ARG INDUSTRYFLOW_REVISION=unknown$", df, re.M), f"{rel}: no ARG INDUSTRYFLOW_REVISION"
        assert re.search(r"^ENV INDUSTRYFLOW_REVISION=\$\{INDUSTRYFLOW_REVISION\}$", df, re.M), \
            f"{rel}: the build argument is not kept in the environment"


def test_every_build_passes_the_commit():
    for r in REPORTERS:
        for name, build in _compose_services(r["dockerfile"]).items():
            assert (build.get("args") or {}).get("INDUSTRYFLOW_REVISION") == "${INDUSTRYFLOW_REVISION:-unknown}", \
                f"compose {name}: the build does not pass INDUSTRYFLOW_REVISION"
    images = (ROOT / ".github/workflows/images.yml").read_text()
    assert re.search(r"^\s+INDUSTRYFLOW_REVISION=\$\{\{ github\.sha \}\}$", images, re.M), \
        "images.yml: the CI build does not pass the commit"
    deploy = (ROOT / "scripts/box-deploy.sh").read_text()
    build_at = deploy.index('docker compose "${PROFILES[@]}" build')
    assert 'export INDUSTRYFLOW_REVISION="$sha"' in deploy[:build_at], \
        "box-deploy.sh: the commit is not exported before the build"


def test_every_served_revision_is_scraped():
    """A driver serves the series on its own port; compose Prometheus and the chart must read it there."""
    jobs = load_yaml("infrastructure/prometheus/prometheus.yml")["scrape_configs"]
    targets = {t for j in jobs if j.get("metrics_path", "/metrics") == "/metrics"
               for c in j.get("static_configs", []) for t in c["targets"]}
    apps = load_yaml("deploy/helm/industryflow/values.yaml")["apps"]
    for r in REPORTERS:
        if r["call"] is not SERVE:
            continue
        component = _component(r)
        assert f"{component}:{DEFAULT_SERVE_PORT}" in targets, f"prometheus.yml: {component} is not scraped"
        app = apps[component]
        assert app.get("buildInfoPort") == DEFAULT_SERVE_PORT and app.get("metrics"), \
            f"values.yaml apps.{component}: buildInfoPort is not {DEFAULT_SERVE_PORT}"
        assert re.search(rf"^EXPOSE {DEFAULT_SERVE_PORT}$", r["dockerfile"].read_text(), re.M), \
            f"{r['dockerfile'].relative_to(ROOT)}: the port is not exposed"
