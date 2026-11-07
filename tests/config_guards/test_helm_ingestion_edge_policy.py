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

import subprocess

import pytest
from _repo import find, helm_template, load_yaml

VALUES = load_yaml("deploy/helm/industryflow/values.yaml")


def _edge_policy(*args):
    return find(helm_template(*args), "NetworkPolicy", "-ingestion-from-edge-only")


def test_the_policy_is_on_by_default():
    assert VALUES["networkPolicy"]["enabled"] is True
    _edge_policy()


def test_only_the_controller_pods_are_admitted():
    np = VALUES["networkPolicy"]
    labels = np["ingressControllerPodLabels"]
    assert labels, "no controller pod labels: the policy would admit the whole namespace"
    rules = _edge_policy()["spec"]["ingress"]
    assert len(rules) == 1, f"one ingress rule (the controller), found {len(rules)}"
    peers = rules[0]["from"]
    assert len(peers) == 1, f"one peer (the controller), found {peers}"
    peer = peers[0]
    assert peer.get("namespaceSelector", {}).get("matchLabels") == {
        "kubernetes.io/metadata.name": np["ingressControllerNamespace"]}
    assert peer.get("podSelector", {}).get("matchLabels") == labels, (
        "the controller is selected by namespace only: every pod in that namespace reaches ingestion"
    )


def test_an_empty_controller_selector_refuses_to_render():
    with pytest.raises(subprocess.CalledProcessError) as err:
        helm_template("--set", "networkPolicy.ingressControllerPodLabels=null")
    assert "ingressControllerPodLabels is empty" in err.value.stderr


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
