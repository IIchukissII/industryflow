# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""A device reaches the same paths through either deployment's device edge (ADR-0002 dec 8).

The compose edge and the cluster's device Ingress each list what they pass to ingestion; a path one
adds and the other lacks works in one deployment only, and a catch-all serves devices /metrics and the
API docs. Both sides are read: the compose edge's mTLS server and the rendered Ingress.
"""

from __future__ import annotations

import re

from _repo import ROOT, find, helm_template

EDGE = (ROOT / "services/ingestion_edge/nginx.conf").read_text()


def _device_server() -> str:
    servers = re.split(r"\nserver \{", EDGE)
    [mtls] = [s for s in servers if re.search(r"^\s*ssl_verify_client\s+on;", s, re.M)]
    return mtls


def _compose_paths() -> set[str]:
    server = _device_server()
    blocks = re.split(r"\n    location ", server)
    proxied = [b for b in blocks if "proxy_pass" in b]
    assert len(proxied) == 1, "the device server proxies from one location"
    m = re.match(r"~ \^/\(([^)]*)\)\$ \{", proxied[0])
    assert m, f"the proxying location is not one list of exact paths: {proxied[0].splitlines()[0]}"
    return {"/" + p for p in m.group(1).split("|")}


def test_the_compose_edge_refuses_every_other_path():
    server = _device_server()
    assert re.search(r"location / \{ return 404; \}", server)
    assert server.count("proxy_pass") == 1


def test_both_device_edges_serve_the_same_paths():
    ingest = find(helm_template("--set", "ingress.mtls.enabled=true"), "Ingress", "-ingest")
    helm = {p["path"] for rule in ingest["spec"]["rules"] for p in rule["http"]["paths"]}
    assert _compose_paths() == helm == {"/ingest", "/health", "/stats"}
