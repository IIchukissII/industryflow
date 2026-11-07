# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A network policy admits only the ingress controller to ingestion, by default (ADR-0009 dec 5).

ADR-0002 dec 3 lets ingestion trust identity headers only because nothing but its edge reaches it.
A policy that is off by default, or that admits every pod of the controller's namespace, leaves any
pod there (or anywhere) free to send headers the service believes. And a policy that is on must not
silently cut a restricted workload off from an external datastore.
"""

from __future__ import annotations

import re
import subprocess

import pytest
from _repo import find, helm_template, load_yaml

VALUES = load_yaml("deploy/helm/industryflow/values.yaml")


def _edge_policy(*args):
    return find(helm_template(*args), "NetworkPolicy", "-ingestion-from-edge-only")


def test_the_policy_is_on_by_default():
    assert VALUES["networkPolicy"]["enabled"] is True
    _edge_policy()


APP = VALUES["apps"]["ingestion-service"]


def _admitted() -> dict[tuple, set[int]]:
    """Each admitted peer (namespace, pod labels) and the ports it may reach."""
    out: dict[tuple, set[int]] = {}
    for rule in _edge_policy()["spec"]["ingress"]:
        ports = {p["port"] for p in rule.get("ports", [])}
        assert ports, f"a rule with no ports admits every port: {rule}"
        for peer in rule["from"]:
            ns = peer.get("namespaceSelector", {}).get("matchLabels")
            pods = peer.get("podSelector", {}).get("matchLabels")
            assert ns and pods, f"a peer selected by namespace or pods alone: {peer}"
            key = (ns["kubernetes.io/metadata.name"], tuple(sorted(pods.items())))
            out.setdefault(key, set()).update(ports)
    return out


def test_the_controller_reaches_the_app_port_and_prometheus_the_metrics_port_only():
    np, obs = VALUES["networkPolicy"], VALUES["observability"]
    controller = (np["ingressControllerNamespace"], tuple(sorted(np["ingressControllerPodLabels"].items())))
    prometheus = (obs["monitoringNamespace"], tuple(sorted(obs["prometheusPodLabels"].items())))
    listener = APP["metricsListener"]["port"]
    assert listener != APP["port"]
    assert _admitted() == {controller: {APP["port"]}, prometheus: {listener}}


@pytest.mark.parametrize("key", ["networkPolicy.ingressControllerPodLabels", "observability.prometheusPodLabels"])
def test_an_empty_pod_selector_refuses_to_render(key):
    with pytest.raises(subprocess.CalledProcessError) as err:
        helm_template("--set", f"{key}=null")
    assert f"{key.split('.')[-1]} is empty" in err.value.stderr


def _ingestion(docs, kind, suffix="-ingestion-service"):
    return find(docs, kind, suffix)


def test_the_metrics_listener_serves_nothing_but_metrics():
    docs = helm_template()
    conf = _ingestion(docs, "ConfigMap", "-ingestion-service-metrics-listener")["data"]["nginx.conf"]
    servers = re.findall(r"\bserver\s*\{", conf)
    assert len(servers) == 1, "one server block"
    assert re.findall(r"^\s*listen\s+(\S+?);", conf, re.M) == [str(APP["metricsListener"]["port"])]
    by_path = {}
    for m in re.finditer(r"location\s+([^{]+?)\s*\{", conf):
        depth, i = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(conf[i], 0)
            i += 1
        by_path[m.group(1).strip()] = conf[m.end():i - 1]
    assert set(by_path) == {"= /metrics", "/"}, f"locations: {sorted(by_path)}"
    assert re.search(rf"proxy_pass\s+http://127\.0\.0\.1:{APP['port']}/metrics;", by_path["= /metrics"])
    assert re.fullmatch(r"\s*return 404;\s*", by_path["/"]), by_path["/"]
    assert conf.count("proxy_pass") == 1
    deployment = _ingestion(docs, "Deployment")
    sidecar = next(c for c in deployment["spec"]["template"]["spec"]["containers"] if c["name"] == "metrics-listener")
    assert sidecar["image"] == APP["metricsListener"]["image"]
    assert [p["containerPort"] for p in sidecar["ports"]] == [APP["metricsListener"]["port"]]


def test_the_servicemonitor_scrapes_the_metrics_listener():
    docs = helm_template("--set", "observability.serviceMonitors.enabled=true")
    service = _ingestion(docs, "Service")
    ports = {p["name"]: p for p in service["spec"]["ports"]}
    assert ports["metrics"]["port"] == APP["metricsListener"]["port"] and ports["metrics"]["targetPort"] == "metrics"
    endpoints = _ingestion(docs, "ServiceMonitor")["spec"]["endpoints"]
    assert {e["port"] for e in endpoints} == {"metrics"}, f"scraped on {sorted(e['port'] for e in endpoints)}"


def test_nothing_but_the_device_edge_routes_to_ingestion():
    docs = helm_template("--set", "ingress.mtls.enabled=true")
    routes = {
        d["metadata"]["name"]
        for d in docs if d.get("kind") == "Ingress"
        for rule in d["spec"]["rules"] for path in rule["http"]["paths"]
        if path["backend"]["service"]["name"].endswith("-ingestion-service")
    }
    assert routes == {"if-industryflow-ingest"}, f"ingestion routed by {sorted(routes)}"
    ingest = find(docs, "Ingress", "-ingest")
    assert ingest["metadata"]["annotations"]["nginx.ingress.kubernetes.io/auth-tls-verify-client"] == "on"


@pytest.mark.parametrize("dep, extra", [
    ("kafka", ["--set", "infra.kafka.external.bootstrap=broker.example:9092"]),
    ("timescaledb", ["--set", "infra.timescaledb.external.host=db.example"]),
    ("redis", ["--set", "infra.redis.external.host=redis.example"]),
])
def test_an_external_dependency_needs_its_egress_listed(dep, extra):
    base = ["--set", f"infra.{dep}.inCluster=false", *extra]
    with pytest.raises(subprocess.CalledProcessError) as err:
        helm_template(*base)
    assert f"networkPolicy.externalEgress.{dep}" in err.value.stderr
    docs = helm_template(*base, "--set-json", f'networkPolicy.externalEgress.{dep}=[{{"cidr":"192.0.2.10/32","port":1}}]')
    blocks = [
        peer["ipBlock"]["cidr"]
        for d in docs if d.get("kind") == "NetworkPolicy"
        for rule in d["spec"].get("egress", []) for peer in rule.get("to", []) if "ipBlock" in peer
    ]
    assert "192.0.2.10/32" in blocks
