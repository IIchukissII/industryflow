# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Each deployment's edge and ingestion-service agree on ONE identity-header convention
(ADR-0002 dec 3), and the service is reachable only from its edge.

The service reads only the convention its deployment names, and only the edge's own headers
are trustworthy. These read both sides from the deployment artifacts, so changing one side
alone (an edge that stops overwriting, a service set to the wrong convention, ingestion back
on the shared network) fails here instead of opening a smuggling path.
"""
import os
import re

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def _read(*parts: str) -> str:
    with open(os.path.join(REPO, *parts), encoding="utf-8") as f:
        return f.read()


def _compose_service(name: str) -> str:
    """The text of one top-level compose service block."""
    text = _read("docker-compose.yml")
    m = re.search(rf"^  {re.escape(name)}:\n(.*?)(?=^  \S|^\S)", text, re.M | re.S)
    assert m, f"service {name} not found in docker-compose.yml"
    return m.group(1)


def _list_under(block: str, key: str) -> list[str]:
    """The entries under ``key``, in either compose form: a list (``- name``) or a mapping
    (``name:`` with per-entry settings such as network aliases)."""
    m = re.search(rf"^    {key}:\n((?:      .*\n)+)", block, re.M)
    assert m, f"{key} not found"
    entries = re.findall(r"^      (?:- (\S+)|([A-Za-z0-9_.-]+):)", m.group(1), re.M)
    return [listed or mapped for listed, mapped in entries]


def test_compose_edge_and_service_use_x_client():
    service = _compose_service("ingestion-service")
    assert re.search(r"^      DEVICE_IDENTITY_HEADERS: x-client$", service, re.M)

    nginx = _read("services", "ingestion_edge", "nginx.conf")
    # The edge sets its own convention from the verified handshake...
    assert re.search(r"proxy_set_header X-Client-Verify \$ssl_client_verify;", nginx)
    assert re.search(r"proxy_set_header X-Client-Cert\s+\$ssl_client_escaped_cert;", nginx)
    # ...and drops the other convention's headers a client may have sent.
    for h in ("ssl-client-verify", "ssl-client-cert", "ssl-client-subject-dn", "ssl-client-issuer-dn"):
        assert re.search(rf'proxy_set_header {h}\s+"";', nginx), h


def test_helm_service_uses_the_ingress_nginx_convention():
    values = _read("deploy", "helm", "industryflow", "values.yaml")
    assert re.search(r"^  DEVICE_IDENTITY_HEADERS: ssl-client$", values, re.M)

    ingress = _read("deploy", "helm", "industryflow", "templates", "ingress.yaml")
    # ingress-nginx sets ssl-client-verify whenever auth-tls is on, and ssl-client-cert only
    # when it passes the certificate upstream; both are needed.
    assert 'auth-tls-verify-client: "on"' in ingress
    assert 'auth-tls-pass-certificate-to-upstream: "true"' in ingress


def test_compose_ingestion_reachable_only_from_the_edge():
    service_nets = _list_under(_compose_service("ingestion-service"), "networks")
    assert "industryflow-network" not in service_nets
    assert sorted(service_nets) == ["ingestion-back", "ingestion-front"]

    # Who else shares each network: the edge in front, Kafka behind, nobody else.
    text = _read("docker-compose.yml")
    services = re.findall(r"^  ([a-z0-9-]+):\n", text.split("\nvolumes:")[0], re.M)
    members = {"ingestion-front": set(), "ingestion-back": set()}
    for name in services:
        block = _compose_service(name)
        if re.search(r"^    networks:", block, re.M):
            for net in _list_under(block, "networks"):
                if net in members:
                    members[net].add(name)
    assert members["ingestion-front"] == {"ingestion-edge", "ingestion-service"}
    assert members["ingestion-back"] == {"kafka", "ingestion-service"}

    for net in members:
        assert re.search(rf"^  {net}:\n(?:    .*\n)*?    internal: true$", text, re.M), net
