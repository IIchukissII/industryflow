# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every image the platform builds that runs platform code and serves metrics reports the revision it
was built from (ADR-0016 dec 8): the Python web services through their own /metrics, the Spark drivers
and the long-lived workers that serve no metrics on a port of their own. Each link is a place an omission fails silently: a process that never calls the
report, a Dockerfile that drops the build argument, a build (compose, CI, the box deploy) that never
passes it, a scrape (compose Prometheus, the chart) that never reads the port. The series then reads
`unknown`, or is missing, and a deploy leaves no trace."""
from __future__ import annotations

import re
from pathlib import Path

from _repo import ROOT, find, helm_template, load_yaml

from industryflow_contracts.build_info import DEFAULT_SERVE_PORT

COMPOSE = load_yaml("docker-compose.yml")["services"]
REPORT = re.compile(r'^build_info\.report\("([a-z0-9-]+)"\)$', re.M)
SERVE = re.compile(r'^\s*build_info\.serve\("([a-z0-9-]+)"\)$', re.M)
SPARK = ROOT / "services/spark_jobs"


def _web_service(main: Path) -> dict:
    return {"entry": main, "dockerfile": main.parent / "Dockerfile", "call": REPORT}


def _worker(main: Path) -> dict:
    return {"entry": main, "dockerfile": main.parent / "Dockerfile", "call": SERVE}


def _spark_driver(dockerfile: Path) -> dict:
    job = re.findall(r'"/app/spark_streaming/(\w+\.py)"\]', dockerfile.read_text())
    return {"entry": SPARK / job[-1], "dockerfile": dockerfile, "call": SERVE}


# The web services serve /metrics through the instrumentator; each Spark job image runs a Python driver
# (its CMD ends in a job script). Every other built image is in EXEMPT below, with its reason.
REPORTERS = sorted(
    [_web_service(p) for p in ROOT.glob("services/**/main.py")
     if "node_modules" not in p.parts and "Instrumentator" in p.read_text()]
    + [_worker(p) for p in ROOT.glob("services/**/main.py")
       if "node_modules" not in p.parts and SERVE.search(p.read_text())]
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
    assert {_component(r) for r in REPORTERS if r["call"] is SERVE} == \
        {"spark-streaming", "spark-aggregations", "alert-service-detector"}


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
    chart = helm_template("--set", "observability.serviceMonitors.enabled=true")
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
        # The chart as rendered: the pod, its Service and its ServiceMonitor each name the port.
        pod = find(chart, "Deployment", f"-{component}")["spec"]["template"]["spec"]["containers"][0]
        assert {"name": "build-info", "containerPort": DEFAULT_SERVE_PORT} in pod.get("ports", []), \
            f"chart: the {component} container does not declare the build-info port"
        service = find(chart, "Service", f"-{component}")["spec"]["ports"]
        assert any(p["name"] == "build-info" and p["port"] == DEFAULT_SERVE_PORT for p in service), \
            f"chart: the {component} Service has no build-info port"
        endpoints = find(chart, "ServiceMonitor", f"-{component}")["spec"]["endpoints"]
        assert any(e["port"] == "build-info" and e["path"] == "/metrics" for e in endpoints), \
            f"chart: the {component} ServiceMonitor does not scrape build-info"


# Every image the platform builds that is not a reporter, and why dec 8 does not reach it. A new image
# is in neither list until someone decides which it is; discovering reporters by how they serve metrics
# alone would miss one that serves them another way.
EXEMPT = {
    "frontend": "serves static files; no metrics",
    "mlflow": "the stock tracking server; serves no metrics the platform scrapes",
    "spark-worker": "the stock worker; executors run only code the driver ships, and the driver reports",
    "notebook-hub": "the stock hub with platform configuration; its own metrics are not scraped",
    "notebook-sql-proxy": "serves no metrics",
    "notebook-tracking-gateway": "serves no metrics",
    "notebook-cold-broker": "serves no metrics",
    "ops-agent": "serves no metrics",
    "notebook-authoring": "a user's kernel, not a platform process",
    "notebook-analytics": "a user's kernel, not a platform process",
    "db-backup": "one-shot job; its success time reaches the metrics store as a file, not a process",
    "cold-export": "one-shot job; serves no metrics",
    "tenant-delete": "one-shot job; serves no metrics",
    "tracking-collect": "one-shot job on the tracking server's image; serves no metrics",
}


def _built_images() -> dict[str, Path]:
    """Every image a deployment builds: compose's builds and the CI image matrix, by service name."""
    built = {}
    for name, svc in COMPOSE.items():
        build = svc.get("build")
        if isinstance(build, dict):
            built[name] = (ROOT / build["context"] / build.get("dockerfile", "Dockerfile")).resolve()
    for entry in load_yaml(".github/workflows/images.yml")["jobs"]["build-scan"]["strategy"]["matrix"]["include"]:
        built.setdefault(entry["name"], (ROOT / entry["file"]).resolve())
    return built


def _reporter_names() -> set[str]:
    return {name for r in REPORTERS for name in _compose_services(r["dockerfile"])}


def test_every_built_image_reports_its_revision_or_is_exempt_by_name():
    built, reporters = _built_images(), _reporter_names()
    undecided = sorted(n for n in built if n not in reporters and n not in EXEMPT)
    assert not undecided, f"built images neither reporting build_info nor exempt (ADR-0016 dec 8): {undecided}"
    stale = sorted(n for n in EXEMPT if n not in built or n in reporters)
    assert not stale, f"exempt entries that are not built, or that report: {stale}"


def test_no_exempt_image_is_scraped():
    """An exempt image that is scraped serves metrics after all, so its exemption no longer holds."""
    jobs = load_yaml("infrastructure/prometheus/prometheus.yml")["scrape_configs"]
    hosts = {t.rsplit(":", 1)[0] for j in jobs for c in j.get("static_configs", []) for t in c["targets"]}
    assert not sorted(hosts & EXEMPT.keys()), "compose Prometheus scrapes an exempt image"
    apps = load_yaml("deploy/helm/industryflow/values.yaml")["apps"]
    assert not sorted(n for n, a in apps.items() if n in EXEMPT and a.get("metrics")), \
        "the chart scrapes an exempt image"
    chart = helm_template("--set", "observability.serviceMonitors.enabled=true")
    monitored = {d["metadata"]["name"] for d in chart if d.get("kind") in ("ServiceMonitor", "PodMonitor")}
    assert not sorted(n for n in EXEMPT for m in monitored if m.endswith(f"-{n}")), \
        "the chart monitors an exempt image"


def test_a_worker_that_serves_no_metrics_serves_the_series_alone():
    """A worker reports its revision on its one listener and nothing else rides on it (ADR-0016 dec 8,
    ADR-0031 dec 2): no other series, no published port, no other Service or scrape port."""
    apps = load_yaml("deploy/helm/industryflow/values.yaml")["apps"]
    workers = [r for r in REPORTERS if r["call"] is SERVE and not str(r["entry"]).startswith(str(SPARK))]
    assert workers, "no worker found: the guard is not looking"
    for r in workers:
        component, code = _component(r), r["entry"].parent
        others = sorted(str(p.relative_to(ROOT)) for p in code.rglob("*.py")
                        if "tests" not in p.parts and re.search(r"^\s*(from|import) prometheus_client", p.read_text(), re.M))
        assert not others, f"{component}: serves metrics beyond the revision series: {others}"
        assert "ports" not in COMPOSE[component], f"compose {component}: the listener is published beyond the network"
        app = apps[component]
        assert not app.get("port") and not app.get("metricsPort"), f"values.yaml apps.{component}: a port beside build-info"
