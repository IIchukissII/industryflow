# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The tenant-delete job reads the device CA's public files and never its keys (ADR-0007 dec 8).

The job verifies a deleted tenant's device certificates are revoked from the chain, the CRL bundle
and the signed issued register (ADR-0033 dec 4). The CA directory also holds the root and issuing
keys, so compose mounts only the chain and the CRL directory, and the chart mounts the device-CA
Secret the edge already reads. Each path the job is given must lie inside what is mounted.
"""

from __future__ import annotations

import posixpath

from _repo import helm_template, load_yaml

ENV = ("TENANT_DELETE_DEVICE_CA_CHAIN", "TENANT_DELETE_DEVICE_CA_CRL", "TENANT_DELETE_DEVICE_CA_REGISTER")


def _inside(path, mounts):
    return any(path == m or path.startswith(m.rstrip("/") + "/") for m in mounts)


def test_compose_mounts_only_the_chain_and_the_crl_directory():
    svc = load_yaml("docker-compose.yml")["services"]["tenant-delete"]
    ca = [v for v in svc["volumes"] if isinstance(v, str) and "DEVICE_CA_DIR" in v]
    # "${DEVICE_CA_DIR:-...}/<source>:<target>:ro"
    binds = [v.split("}", 1)[1].split(":") for v in ca]
    assert sorted(posixpath.relpath(source, "/") for source, _, _ in binds) == ["ca-chain.crt", "crl"], ca
    assert all(mode == "ro" for _, _, mode in binds)
    targets = [target for _, target, _ in binds]
    for name in ENV:
        assert _inside(svc["environment"][name], targets), name


def _job(docs):
    [job] = [d for d in docs if d.get("kind") == "CronJob" and d["metadata"]["name"].endswith("-tenant-delete")]
    return job["spec"]["jobTemplate"]["spec"]["template"]["spec"]


def test_the_chart_mounts_the_edges_device_ca_secret_when_the_edge_verifies_devices():
    pod = _job(helm_template("--set", "ingress.mtls.enabled=true", "--set", "ingress.mtls.caSecret=dev-ca"))
    [volume] = [v for v in pod["volumes"] if v.get("secret", {}).get("secretName") == "dev-ca"]
    container = pod["containers"][0]
    [mount] = [m for m in container["volumeMounts"] if m["name"] == volume["name"]]
    assert mount["readOnly"] is True
    env = {e["name"]: e.get("value") for e in container["env"]}
    for name in ENV:
        assert _inside(env[name], [mount["mountPath"]]), name


def test_without_device_verification_the_step_is_not_offered():
    container = _job(helm_template("--set", "ingress.mtls.enabled=false"))["containers"][0]
    assert not {e["name"] for e in container["env"]} & set(ENV)
