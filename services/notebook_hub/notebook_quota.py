# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The notebook pool on Kubernetes (ADR-0050 dec 7, ADR-0018 dec 6): a ResourceQuota on the notebook sessions'
memory requests, kept at what the cluster's nodes allocate beyond every other pod's requests.

    python notebook_quota.py      # the chart's notebook-quota CronJob

The quota is scoped to the notebook sessions' PriorityClass, so it counts only their requests in a namespace
the platform's pods share. Its size is read from the live cluster, not from the chart's values: the nodes'
allocatable memory, less the requests of every pod that is not a session, in every namespace. The values
know neither how many replicas an autoscaler runs nor what the cluster's own pods request; the scheduler
places by the live requests, so the pool is taken from them. A node added raises it at the next run.

The hub reads the quota's size when a session starts (``read_pool``): a session's limit is the whole pool,
and its request its profile's guarantee, which the quota admits against the others'.

Reads NOTEBOOK_QUOTA_NAME, NOTEBOOK_PRIORITY_CLASS and NOTEBOOK_NAMESPACE.
"""
from __future__ import annotations

import asyncio
import os
import re
import sys

_UNITS = {"": 1, "k": 10**3, "M": 10**6, "G": 10**9, "T": 10**12, "P": 10**15, "E": 10**18,
          "Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40, "Pi": 2**50, "Ei": 2**60, "m": 0.001}


def parse_quantity(text) -> int:
    """A Kubernetes memory quantity in bytes: ``4Gi``, ``512M``, ``1e9``, ``134217728``."""
    m = re.fullmatch(r"([0-9.]+(?:[eE][0-9]+)?)([a-zA-Z]*)", str(text).strip())
    if not m or m.group(2) not in _UNITS:
        raise ValueError(f"not a memory quantity: {text!r}")
    return int(float(m.group(1)) * _UNITS[m.group(2)])


def usable(node) -> bool:
    """Whether a session can be placed on the node: schedulable, ready, and no taint that keeps pods off it
    (sessions tolerate none)."""
    if getattr(node.spec, "unschedulable", False):
        return False
    ready = [c for c in (node.status.conditions or []) if c.type == "Ready"]
    if not ready or ready[0].status != "True":
        return False
    return not any(t.effect in ("NoSchedule", "NoExecute") for t in (node.spec.taints or []))


def _memory(node) -> int:
    return parse_quantity((node.status.allocatable or {}).get("memory", "0"))


def pool_bytes(nodes, pods, notebook_class: str) -> tuple[int, int]:
    """What the nodes a session can be placed on allocate beyond the requests of every other pod that runs
    or waits on them, and the largest of those nodes: one session never holds more than one node."""
    placed = [n for n in nodes if usable(n)]
    names = {n.metadata.name for n in placed}
    held = 0
    for p in pods:
        if p.status.phase not in ("Pending", "Running") or p.spec.priority_class_name == notebook_class:
            continue
        # A pod on a node sessions cannot use takes nothing from them; one not yet placed may take from any.
        if p.spec.node_name and p.spec.node_name not in names:
            continue
        for c in p.spec.containers:
            requests = (c.resources.requests or {}) if c.resources else {}
            held += parse_quantity(requests.get("memory", "0"))
    return max(0, sum(_memory(n) for n in placed) - held), max((_memory(n) for n in placed), default=0)


class NoPool(Exception):
    """The notebook quota cannot be read, or has not been computed yet."""


# The largest node a session can be placed on, beside the pool on the quota: a session's limit is the pool,
# never more than one node holds.
LARGEST_NODE = "industryflow.io/largest-node-memory"


async def read_pool(api, namespace: str, name: str) -> int:
    """A session's limit in bytes: the quota's hard ``requests.memory``, at most the largest node a session can
    be placed on; ``NoPool`` when the quota cannot say, or holds none."""
    from kubernetes_asyncio.client.exceptions import ApiException

    try:
        quota = await api.read_namespaced_resource_quota(name, namespace)
    except ApiException as e:
        raise NoPool(f"the notebook quota {namespace}/{name} cannot be read ({e.status})") from None
    hard = (quota.spec.hard or {}).get("requests.memory") if quota.spec else None
    if hard is None or parse_quantity(hard) == 0:
        raise NoPool(f"the notebook quota {namespace}/{name} holds no memory yet")
    largest = ((quota.metadata.annotations or {}) if quota.metadata else {}).get(LARGEST_NODE)
    return min(parse_quantity(hard), parse_quantity(largest)) if largest else parse_quantity(hard)


def quota_body(name: str, notebook_class: str, pool: int, largest: int) -> dict:
    """The quota, scoped to the notebook sessions' PriorityClass, holding ``pool`` bytes of requests, with the
    largest node a session can be placed on."""
    return {
        "apiVersion": "v1", "kind": "ResourceQuota",
        "metadata": {"name": name, "labels": {"app.kubernetes.io/name": "notebook-quota"},
                     "annotations": {LARGEST_NODE: str(largest)}},
        "spec": {
            "hard": {"requests.memory": str(pool)},
            "scopeSelector": {"matchExpressions": [
                {"operator": "In", "scopeName": "PriorityClass", "values": [notebook_class]}]},
        },
    }


async def apply(core, namespace: str, name: str, notebook_class: str) -> int:
    """Compute the pool from the live cluster and write the quota; returns the pool in bytes."""
    from kubernetes_asyncio.client.exceptions import ApiException

    nodes = (await core.list_node()).items
    pods = (await core.list_pod_for_all_namespaces()).items
    pool, largest = pool_bytes(nodes, pods, notebook_class)
    body = quota_body(name, notebook_class, pool, largest)
    try:
        await core.replace_namespaced_resource_quota(name, namespace, body)
    except ApiException as e:
        if e.status != 404:
            raise
        await core.create_namespaced_resource_quota(namespace, body)
    return pool


async def _main() -> int:
    from kubernetes_asyncio import client, config

    config.load_incluster_config()
    env = {k: os.environ.get(k, "").strip() for k in ("NOTEBOOK_QUOTA_NAME", "NOTEBOOK_PRIORITY_CLASS",
                                                       "NOTEBOOK_NAMESPACE")}
    missing = [k for k, v in env.items() if not v]
    if missing:
        raise SystemExit(f"notebook quota: {', '.join(missing)} must be set")
    async with client.ApiClient() as api:
        pool = await apply(client.CoreV1Api(api), env["NOTEBOOK_NAMESPACE"], env["NOTEBOOK_QUOTA_NAME"],
                           env["NOTEBOOK_PRIORITY_CLASS"])
    print(f"notebook quota {env['NOTEBOOK_NAMESPACE']}/{env['NOTEBOOK_QUOTA_NAME']}: {pool >> 20} MiB", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_main()))
