# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Both deployments grade an approaching device-CA expiry by what it refuses (ADR-0007 dec 10).

A CRL or CA certificate near expiry refuses every device; a device's renewal owed refuses one. One
signal for both (a healthcheck running the two checks together) says neither which nor how bad. So
Compose runs the two checks apart, records each one's last success where its Prometheus reads it, and
raises the same alerts at the same severities as the chart does from its two CronJobs.
"""

from __future__ import annotations

import re

from _repo import helm_template, load_yaml

COMPOSE = load_yaml("docker-compose.yml")["services"]
WATCH = COMPOSE["device-crl-refresh"]
RULES = [r for g in load_yaml("infrastructure/prometheus/rules/compose.yml")["groups"] for r in g["rules"]]
GRADES = {"IndustryFlowDeviceCrlStale": ("crl", "check"), "IndustryFlowDeviceCertExpiring": ("certs", "check-certs")}


def _script() -> str:
    return "\n".join(WATCH["command"]) if isinstance(WATCH["command"], list) else WATCH["command"]


def test_the_healthcheck_is_not_one_signal_for_both_checks():
    test = " ".join(WATCH["healthcheck"]["test"])
    assert "check-certs" not in test, "the healthcheck joins the device renewal to the CRL check"


def test_each_check_records_its_own_success():
    script = _script()
    for label, command in GRADES.values():
        assert re.search(rf"if sh /opt/device-ca\.sh {re.escape(command)}; then mark {label}\b", script), (
            f"`{command}` does not record its success as check={label}"
        )


def test_node_exporter_reads_what_the_watch_writes():
    script = _script()
    [written] = set(re.findall(r'"(/[\w/-]+)/device-ca-\$\$1\.prom"', script))
    volume = next(v.split(":")[0] for v in WATCH["volumes"] if v.split(":")[1] == written)
    exporter = COMPOSE["node-exporter"]
    read_at = next(v.split(":")[1] for v in exporter["volumes"] if v.split(":")[0] == volume)
    assert f"--collector.textfile.directory={read_at}" in exporter["command"], (
        f"node-exporter does not collect {read_at}, where the watch's volume {volume} is mounted"
    )


def test_compose_raises_each_grade_from_its_check():
    metric = re.search(r"(industryflow_\w+_last_success_timestamp_seconds)\{check=", _script()).group(1)
    for alert, (label, _) in GRADES.items():
        rules = [r for r in RULES if r["alert"] == alert]
        assert rules, f"compose raises no {alert}"
        for rule in rules:
            assert metric in rule["expr"] and f'check="{label}"' in rule["expr"], (alert, rule["expr"])
        assert any("absent(" in r["expr"] for r in rules), f"{alert}: a check that never passed is not raised"


def test_compose_grades_as_the_chart_does():
    docs = helm_template("--set", "observability.prometheusRules.enabled=true",
                         "--set", "ingress.enabled=true", "--set", "ingress.mtls.enabled=true")
    rule = next(d for d in docs if d["kind"] == "PrometheusRule")
    chart = {r["alert"]: r["labels"]["severity"] for g in rule["spec"]["groups"] for r in g["rules"] if r["alert"] in GRADES}
    compose = {r["alert"]: r["labels"]["severity"] for r in RULES if r["alert"] in GRADES}
    assert chart == compose == {"IndustryFlowDeviceCrlStale": "critical", "IndustryFlowDeviceCertExpiring": "warning"}, (
        chart, compose)
