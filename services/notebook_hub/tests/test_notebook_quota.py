# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The notebook pool on Kubernetes (ADR-0050 dec 7, ADR-0018 dec 6): what the nodes a session can be placed on
allocate beyond every other pod's requests, held as a quota on the sessions' requests that the hub reads as a
session starts."""
from __future__ import annotations

import asyncio
import pathlib
import sys
from types import SimpleNamespace as NS

import pytest
from kubernetes_asyncio.client.exceptions import ApiException

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import notebook_quota  # noqa: E402

GI = 2**30
CLASS = "industryflow-notebooks"


def _node(name, mem, ready="True", unschedulable=False, taints=()):
    return NS(metadata=NS(name=name),
              spec=NS(unschedulable=unschedulable, taints=[NS(effect=e) for e in taints] or None),
              status=NS(allocatable={"memory": mem}, conditions=[NS(type="Ready", status=ready)]))


def _pod(phase, *requests, cls=None, node="a"):
    containers = [NS(resources=NS(requests={"memory": r} if r else None)) for r in requests]
    return NS(status=NS(phase=phase), spec=NS(priority_class_name=cls, containers=containers, node_name=node))


@pytest.mark.parametrize("text, expected", [("4Gi", 4 * GI), ("512Mi", 512 * 2**20), ("1G", 10**9),
                                            ("134217728", 134217728), ("1e9", 10**9), ("16303836Ki", 16303836 * 1024)])
def test_a_quantity_is_read_in_bytes(text, expected):
    assert notebook_quota.parse_quantity(text) == expected


@pytest.mark.parametrize("text", ["", "4 GiB", "lots", "4Zi"])
def test_anything_else_is_not_a_quantity(text):
    with pytest.raises(ValueError):
        notebook_quota.parse_quantity(text)


def test_the_pool_is_what_the_nodes_allocate_beyond_every_other_pods_requests():
    nodes = [_node("a", "8Gi"), _node("b", "6Gi")]
    pods = [_pod("Running", "1Gi", "512Mi"),            # a platform pod, two containers
            _pod("Pending", "1Gi", node=None),          # not yet placed: it may take from any node
            _pod("Running", None),                      # no request: nothing reserved
            _pod("Succeeded", "4Gi"),                   # finished: reserves nothing
            _pod("Running", "3Gi", cls=CLASS)]          # a session: the quota's own
    assert notebook_quota.pool_bytes(nodes, pods, CLASS) == (14 * GI - int(2.5 * GI), 8 * GI)
    assert notebook_quota.pool_bytes([_node("a", "1Gi")], [_pod("Running", "2Gi")], CLASS) == (0, GI)


@pytest.mark.parametrize("node", [_node("x", "64Gi", unschedulable=True), _node("x", "64Gi", ready="False"),
                                  _node("x", "64Gi", taints=("NoSchedule",)), _node("x", "64Gi", taints=("NoExecute",))])
def test_a_node_no_session_can_be_placed_on_adds_nothing_and_takes_nothing(node):
    nodes = [_node("a", "8Gi"), node]
    pods = [_pod("Running", "2Gi"), _pod("Running", "32Gi", node="x")]
    assert notebook_quota.pool_bytes(nodes, pods, CLASS) == (6 * GI, 8 * GI)


def test_a_preferring_taint_keeps_the_node():
    assert notebook_quota.usable(_node("a", "8Gi", taints=("PreferNoSchedule",)))


def test_the_quota_counts_the_sessions_class_only_and_names_the_largest_node():
    body = notebook_quota.quota_body("q", CLASS, 5 * GI, 4 * GI)
    assert body["spec"]["hard"] == {"requests.memory": str(5 * GI)}
    assert body["spec"]["scopeSelector"]["matchExpressions"] == [
        {"operator": "In", "scopeName": "PriorityClass", "values": [CLASS]}]
    assert body["metadata"]["annotations"] == {notebook_quota.LARGEST_NODE: str(4 * GI)}


class _Api:
    def __init__(self, quota=None, status=None, nodes=(), pods=()):
        self.quota, self.status, self.nodes, self.pods = quota, status, list(nodes), list(pods)
        self.written = []

    async def read_namespaced_resource_quota(self, name, namespace):
        if self.status:
            raise ApiException(status=self.status)
        return self.quota

    async def list_node(self):
        return NS(items=self.nodes)

    async def list_pod_for_all_namespaces(self):
        return NS(items=self.pods)

    async def replace_namespaced_resource_quota(self, name, namespace, body):
        if self.status:
            raise ApiException(status=self.status)
        self.written.append(("replace", body))

    async def create_namespaced_resource_quota(self, namespace, body):
        self.written.append(("create", body))


def _quota(hard, largest=None):
    return NS(spec=NS(hard=hard),
              metadata=NS(annotations={notebook_quota.LARGEST_NODE: largest} if largest else None))


def test_a_sessions_limit_is_the_pool_at_most_one_node():
    assert asyncio.run(notebook_quota.read_pool(_Api(quota=_quota({"requests.memory": "6Gi"})), "ns", "q")) == 6 * GI
    api = _Api(quota=_quota({"requests.memory": "20Gi"}, str(8 * GI)))
    assert asyncio.run(notebook_quota.read_pool(api, "ns", "q")) == 8 * GI


@pytest.mark.parametrize("api, says", [(_Api(status=404), r"cannot be read \(404\)"),
                                       (_Api(status=403), r"cannot be read \(403\)"),
                                       (_Api(quota=_quota({})), "holds no memory yet"),
                                       (_Api(quota=_quota({"requests.memory": "0"})), "holds no memory yet")])
def test_a_pool_the_hub_cannot_read_or_that_holds_nothing_is_no_pool(api, says):
    with pytest.raises(notebook_quota.NoPool, match=says):
        asyncio.run(notebook_quota.read_pool(api, "ns", "q"))


def test_the_job_writes_the_pool_and_creates_the_quota_the_first_time():
    nodes, pods = [_node("a", "16Gi")], [_pod("Running", "6Gi")]
    first = _Api(status=404, nodes=nodes, pods=pods)
    assert asyncio.run(notebook_quota.apply(first, "ns", "q", CLASS)) == 10 * GI
    assert [kind for kind, _ in first.written] == ["create"]
    again = _Api(nodes=nodes, pods=pods)
    asyncio.run(notebook_quota.apply(again, "ns", "q", CLASS))
    assert again.written[0][0] == "replace" and again.written[0][1]["spec"]["hard"]["requests.memory"] == str(10 * GI)
