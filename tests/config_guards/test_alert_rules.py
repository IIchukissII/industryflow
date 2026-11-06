# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The platform alert rules: one file, two deployments, one label (ADR-0016 dec 1, 4).

Each list here fails silently when it misses one entry: a rule selector without the platform label
fires for another stack's series in a shared Prometheus, a scrape target without the label is never
seen by any rule, a chart that renders its own copy of the rules drifts from the file Compose loads,
and a CI gate that names its jobs by hand passes a job it forgot.
"""

from __future__ import annotations

import re

import pytest
from _repo import ROOT, helm_template, load_yaml

RULES = load_yaml("infrastructure/prometheus/rules/platform.yml")
# Every file Compose's Prometheus loads (rule_files: rules/*.yml); the chart ships platform.yml only.
RULE_FILES = sorted((ROOT / "infrastructure" / "prometheus" / "rules").glob("*.yml"))
PROMETHEUS = load_yaml("infrastructure/prometheus/prometheus.yml")
COMPOSE = load_yaml("docker-compose.yml")["services"]
LABEL = 'platform="industryflow"'
FUNCTIONS = {"sum", "increase", "rate", "by", "unless", "offset", "or", "and", "on"}


def _rules():
    rules = [r for f in RULE_FILES for g in load_yaml(str(f.relative_to(ROOT)))["groups"] for r in g["rules"]]
    assert rules, "no rules — the guard is not looking"
    return rules


def test_the_guard_reads_every_rules_file():
    assert {f.name for f in RULE_FILES} >= {"platform.yml", "compose.yml"}


def test_every_rules_file_has_unit_tests_that_ci_runs():
    workflow = (ROOT / ".github" / "workflows" / "config-guards.yml").read_text()
    step = next(line for line in workflow.splitlines() if "test rules" in line and "platform_test.yml" in line)
    for f in RULE_FILES:
        test = f.parent / "tests" / f"{f.stem}_test.yml"
        assert test.exists(), f"{f.name} has no promtool tests ({test.relative_to(ROOT)})"
        assert f"../{f.name}" in load_yaml(str(test.relative_to(ROOT)))["rule_files"], test.name
        assert test.name in step.split(), f"the alert-rules job does not run {test.name}"


@pytest.mark.parametrize("rule", _rules(), ids=lambda r: r["alert"])
def test_every_selector_is_scoped_to_the_platform(rule):
    expr = rule["expr"]
    selectors = re.findall(r"\b([a-z_:][a-z0-9_:]*)\s*(\{[^}]*\})?", expr)
    metrics = [(m, sel) for m, sel in selectors
               if m not in FUNCTIONS and not re.fullmatch(r"\d+[smhd]?", m) and (sel or "_" in m)]
    assert metrics, f"{rule['alert']}: no metric selector found"
    unscoped = [m for m, sel in metrics if LABEL not in sel]
    assert not unscoped, f"{rule['alert']}: selectors without {LABEL}: {unscoped}"


def test_every_compose_scrape_target_carries_the_platform_label():
    jobs = PROMETHEUS["scrape_configs"]
    assert jobs
    missing = [j["job_name"] for j in jobs
               for sc in j.get("static_configs", []) if sc.get("labels", {}).get("platform") != "industryflow"]
    assert not missing, f"scrape jobs no platform rule can see: {missing}"


def test_compose_prometheus_loads_the_rules_it_mounts():
    mounts = [v.split(":") for v in COMPOSE["prometheus"]["volumes"]]
    target = next(dst for src, dst, *_ in mounts if src == "./infrastructure/prometheus/rules")
    assert any(f.startswith(target + "/") for f in PROMETHEUS["rule_files"]), PROMETHEUS["rule_files"]
    assert PROMETHEUS["alerting"]["alertmanagers"][0]["static_configs"][0]["targets"] == ["alertmanager:9093"]
    assert "alertmanager" in COMPOSE


TOGGLES = {
    "rules-only": [],
    "with-mtls": ["--set", "ingress.enabled=true", "--set", "ingress.mtls.enabled=true"],
}


@pytest.mark.parametrize("toggle", TOGGLES)
def test_the_chart_renders_the_same_platform_rules(toggle):
    docs = helm_template("--set", "observability.prometheusRules.enabled=true",
                         "--set", "observability.serviceMonitors.enabled=true", *TOGGLES[toggle])
    rule = next(d for d in docs if d["kind"] == "PrometheusRule")
    rendered = {g["name"]: g for g in rule["spec"]["groups"]}
    for group in RULES["groups"]:
        assert rendered.get(group["name"]) == group, f"the chart's {group['name']} differs from the file Compose loads"
    monitors = [d for d in docs if d["kind"] == "ServiceMonitor"]
    assert monitors
    unlabelled = [d["metadata"]["name"] for d in monitors for e in d["spec"]["endpoints"]
                  if {"targetLabel": "platform", "replacement": "industryflow"} not in e.get("relabelings", [])]
    assert not unlabelled, f"ServiceMonitors no platform rule can see: {unlabelled}"


def test_every_ci_gate_checks_every_job_it_needs():
    loose = []
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for name, job in load_yaml(f".github/workflows/{wf.name}")["jobs"].items():
            if not name.endswith("-gate"):
                continue
            needs = job.get("needs", [])
            needs = [needs] if isinstance(needs, str) else needs
            body = str(job)
            if "toJSON(needs)" in body or "contains(needs" in body:
                continue
            loose += [f"{wf.name}:{name}:{n}" for n in needs if f"needs.{n}.result" not in body]
    assert not loose, f"gates that would pass a job they never check: {loose}"


def test_the_stalled_stream_rule_watches_the_topic_the_platform_publishes():
    # The rule names the tenant-status topic; the contract owns it (ADR-0033 dec 3). A rename on either
    # side, or a deployment that sets another topic, leaves the rule watching a topic nobody writes.
    from industryflow_contracts import tenant_status

    [rule] = [r for r in _rules() if r.get("alert") == "IndustryFlowTenantStatusStalled"]
    topics = set(re.findall(r'topic="([^"]+)"', rule["expr"]))
    assert topics == {tenant_status.DEFAULT_TOPIC}, topics
    setters = sorted(
        str(p.relative_to(ROOT)) for p in [ROOT / "docker-compose.yml", ROOT / ".env.example",
                                           *sorted((ROOT / "deploy" / "helm").rglob("*.yaml"))]
        if "KAFKA_TOPIC_TENANT_STATUS" in p.read_text()
    )
    assert not setters, f"a deployment names its own tenant-status topic; the rule would not see it: {setters}"
