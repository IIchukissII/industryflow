# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The notebook pool on Kubernetes (ADR-0050 dec 7, ADR-0018 dec 6): what the nodes allocate beyond every other
pod's requests, held as a quota on the sessions' requests that the hub reads as a session starts."""
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


def _node(mem):
    return NS(status=NS(allocatable={"memory": mem}))


def _pod(phase, *requests, cls=None):
    containers = [NS(resources=NS(requests={"memory": r} if r else None)) for r in requests]
    return NS(status=NS(phase=phase), spec=NS(priority_class_name=cls, containers=containers))


@pytest.mark.parametrize("text, expected", [("4Gi", 4 * GI), ("512Mi", 512 * 2**20), ("1G", 10**9),
                                            ("134217728", 134217728), ("1e9", 10**9), ("16303836Ki", 16303836 * 1024)])
def test_a_quantity_is_read_in_bytes(text, expected):
    assert notebook_quota.parse_quantity(text) == expected


@pytest.mark.parametrize("text", ["", "4 GiB", "lots", "4Zi"])
def test_anything_else_is_not_a_quantity(text):
    with pytest.raises(ValueError):
        notebook_quota.parse_quantity(text)


def test_the_pool_is_what_the_nodes_allocate_beyond_every_other_pods_requests():
    nodes = [_node("8Gi"), _node("8Gi")]
    pods = [_pod("Running", "1Gi", "512Mi"),          # a platform pod, two containers
            _pod("Pending", "1Gi"),                   # scheduled or not, it is placed by its request
            _pod("Running", None),                    # no request: nothing reserved
            _pod("Succeeded", "4Gi"),                 # finished: reserves nothing
            _pod("Running", "3Gi", cls=CLASS)]        # a session: the quota's own
    assert notebook_quota.pool_bytes(nodes, pods, CLASS) == 16 * GI - int(2.5 * GI)
    assert notebook_quota.pool_bytes([_node("1Gi")], [_pod("Running", "2Gi")], CLASS) == 0


def test_the_quota_counts_the_sessions_class_only():
    body = notebook_quota.quota_body("q", CLASS, 5 * GI)
    assert body["spec"]["hard"] == {"requests.memory": str(5 * GI)}
    assert body["spec"]["scopeSelector"]["matchExpressions"] == [
        {"operator": "In", "scopeName": "PriorityClass", "values": [CLASS]}]


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


def test_the_hub_reads_the_pools_size():
    api = _Api(quota=NS(spec=NS(hard={"requests.memory": "6Gi"})))
    assert asyncio.run(notebook_quota.read_pool(api, "ns", "q")) == 6 * GI


@pytest.mark.parametrize("api, says", [(_Api(status=404), r"cannot be read \(404\)"),
                                       (_Api(status=403), r"cannot be read \(403\)"),
                                       (_Api(quota=NS(spec=NS(hard={}))), "holds no memory yet")])
def test_a_pool_the_hub_cannot_read_is_no_pool(api, says):
    with pytest.raises(notebook_quota.NoPool, match=says):
        asyncio.run(notebook_quota.read_pool(api, "ns", "q"))


def test_the_job_writes_the_pool_and_creates_the_quota_the_first_time():
    nodes, pods = [_node("16Gi")], [_pod("Running", "6Gi")]
    first = _Api(status=404, nodes=nodes, pods=pods)
    assert asyncio.run(notebook_quota.apply(first, "ns", "q", CLASS)) == 10 * GI
    assert [kind for kind, _ in first.written] == ["create"]
    again = _Api(nodes=nodes, pods=pods)
    asyncio.run(notebook_quota.apply(again, "ns", "q", CLASS))
    assert again.written[0][0] == "replace" and again.written[0][1]["spec"]["hard"]["requests.memory"] == str(10 * GI)
