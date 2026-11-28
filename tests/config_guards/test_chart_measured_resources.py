# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The chart's memory comes from the platform's measured need (ADR-0050 dec 7): one measurement serves the
single host and the cluster, and a chart that differs from it fails here.

Both sides: every long-running container the chart renders takes its memory from `measuredResources`, or is
named in the measurement as not yet measured; and every service the measurement says the chart runs is one
the chart renders. The block in the values is exactly what scripts/host-memory.py --helm writes.
"""
from __future__ import annotations

import importlib.util
import re
import tomllib

import pytest
from _repo import ROOT, helm_template

NEED = tomllib.loads((ROOT / "deploy" / "host-memory.toml").read_text())
VALUES = (ROOT / "deploy" / "helm" / "industryflow" / "values.yaml").read_text()

_spec = importlib.util.spec_from_file_location("host_memory", ROOT / "scripts" / "host-memory.py")
host_memory = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(host_memory)

# Every long-running workload the chart can run: the optional ones switched on.
FULL = ("--set", "networkPolicy.enabled=true", "--set", "notebookHub.enabled=true",
        "--set", "observability.opsAgent.enabled=true",
        "--set", "secrets.data.OPS_AGENT_WEBHOOK_SECRET=ci-only-webhook-secret-of-32-chars-or-more",
        "--set", "observability.opsAgent.prometheusUrl=http://prometheus.monitoring:9090",
        "--set", "observability.opsAgent.lokiUrl=http://loki.monitoring:3100",
        "--set", "observability.opsAgent.alertmanagerUrl=http://alertmanager.monitoring:9093")
LONG_RUNNING = ("Deployment", "StatefulSet", "DaemonSet")


@pytest.fixture(scope="module")
def rendered():
    return helm_template(*FULL)


def _containers(docs):
    """``(workload, container, resources)`` of every long-running container; the workload as its compose
    service is named, the chart's prefix taken off."""
    prefix = "if-industryflow-"
    for d in docs:
        if d.get("kind") in LONG_RUNNING:
            workload = d["metadata"]["name"].removeprefix(prefix)
            for c in d["spec"]["template"]["spec"]["containers"]:
                yield workload, c["name"], c.get("resources") or {}


def test_the_values_hold_exactly_what_the_script_writes():
    m = re.search(rf"{re.escape(host_memory.CHART_BEGIN)}.*?{re.escape(host_memory.CHART_END)}", VALUES, flags=re.S)
    assert m, "no measuredResources block: run scripts/host-memory.py --helm"
    assert m.group(0) == host_memory.chart_block(NEED), "measuredResources differs: run scripts/host-memory.py --helm"


def test_every_long_running_container_takes_its_memory_from_the_measurement(rendered):
    measured = host_memory.chart_resources(NEED)["services"]
    unmeasured = set(NEED["chart"]["unmeasured"])
    seen, left = set(), set()
    for workload, container, resources in _containers(rendered):
        memory = ((resources.get("requests") or {}).get("memory"), (resources.get("limits") or {}).get("memory"))
        if f"{workload}/{container}" in unmeasured:
            left.add(f"{workload}/{container}")
            continue
        assert workload in measured, f"{workload}/{container}: no measured need (deploy/host-memory.toml [chart])"
        assert memory == (measured[workload]["requests"]["memory"], measured[workload]["limits"]["memory"]), \
            f"{workload}/{container}: memory {memory} is not its measured need"
        seen.add(workload)
    assert seen == set(NEED["chart"]["services"]), \
        f"measured, not rendered: {sorted(set(NEED['chart']['services']) - seen)}"
    assert left == unmeasured, f"named unmeasured, not rendered: {sorted(unmeasured - left)}"


def test_a_python_probe_is_counted_where_the_chart_runs_one(rendered):
    """A kubelet's HTTP probe runs nothing in the container; an exec probe that runs Python does."""
    probed = set()
    for d in rendered:
        if d.get("kind") in LONG_RUNNING:
            for c in d["spec"]["template"]["spec"]["containers"]:
                command = " ".join(((c.get("livenessProbe") or {}).get("exec") or {}).get("command", []))
                if "python" in command:
                    probed.add(d["metadata"]["name"].removeprefix("if-industryflow-"))
    assert probed == set(NEED["chart"]["python_probes"])


def test_the_heaps_are_of_the_measured_need(rendered):
    heaps = host_memory.chart_resources(NEED)["heaps"]
    env = {}
    for d in rendered:
        if d.get("kind") in LONG_RUNNING:
            for c in d["spec"]["template"]["spec"]["containers"]:
                for e in c.get("env") or []:
                    env.setdefault((d["metadata"]["name"].removeprefix("if-industryflow-"), e["name"]), e.get("value"))
                for a in c.get("args") or []:
                    if "=" in str(a):
                        k, v = str(a).split("=", 1)
                        env.setdefault((d["metadata"]["name"].removeprefix("if-industryflow-"), k), v)
    assert env[("zookeeper", "KAFKA_HEAP_OPTS")] == heaps["ZOOKEEPER_HEAP_OPTS"]
    assert env[("kafka", "KAFKA_HEAP_OPTS")] == heaps["KAFKA_HEAP_OPTS"]
    assert env[("spark-streaming", "SPARK_DRIVER_MEMORY")] == heaps["SPARK_STREAMING_DRIVER_MEMORY"]
    assert env[("spark-aggregations", "SPARK_DRIVER_MEMORY")] == heaps["SPARK_AGGREGATIONS_DRIVER_MEMORY"]
    assert env[("timescaledb", "shared_buffers")] == heaps["POSTGRES_SHARED_BUFFERS"]
    assert env[("timescaledb", "effective_cache_size")] == heaps["POSTGRES_EFFECTIVE_CACHE_SIZE"]
    assert env[("timescaledb", "maintenance_work_mem")] == heaps["POSTGRES_MAINTENANCE_WORK_MEM"]


def test_the_hub_and_the_quota_job_agree_on_the_pool(rendered):
    """ADR-0018 dec 6, ADR-0050 dec 7: the hub's guarantees are the measured ones; the quota the job writes,
    the class it counts and the quota the hub reads are one name; the hub may read only that quota."""
    guarantees = host_memory.guarantees(NEED)
    hub = next(c for d in rendered if d.get("kind") == "Deployment" and d["metadata"]["name"].endswith("notebook-hub")
               for c in d["spec"]["template"]["spec"]["containers"] if c["name"] == "hub")
    env = {e["name"]: e.get("value") for e in hub["env"]}
    assert env["NOTEBOOK_AUTHORING_GUARANTEE_MIB"] == str(guarantees["authoring"])
    assert env["NOTEBOOK_ANALYTICS_GUARANTEE_MIB"] == str(guarantees["analytics"])
    job = next(d for d in rendered if d.get("kind") == "CronJob" and d["metadata"]["name"].endswith("notebooks-quota"))
    jenv = {e["name"]: e["value"] for e in job["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert env["NOTEBOOK_QUOTA_NAME"] == jenv["NOTEBOOK_QUOTA_NAME"] == env["NOTEBOOK_PRIORITY_CLASS"] \
        == jenv["NOTEBOOK_PRIORITY_CLASS"]
    pc = next(d for d in rendered if d.get("kind") == "PriorityClass")
    assert pc["metadata"]["name"] == env["NOTEBOOK_PRIORITY_CLASS"] and pc["preemptionPolicy"] == "Never"
    role = next(d for d in rendered if d.get("kind") == "Role" and d["metadata"]["name"].endswith("-notebook-hub"))
    quota_rules = [r for r in role["rules"] if "resourcequotas" in r["resources"]]
    assert quota_rules == [{"apiGroups": [""], "resources": ["resourcequotas"],
                            "resourceNames": [env["NOTEBOOK_QUOTA_NAME"]], "verbs": ["get"]}]
