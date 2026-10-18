# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The device edge reaches the ingestion service through one kept-alive, re-resolving pool.

A connection per request (a variable proxy_pass) leaves a TIME_WAIT socket each and exhausts the
edge's ephemeral ports under load: connect() fails with "Address not available" and devices get 502.
A static name without `resolve` pins the address nginx saw at start and strands the edge when
ingestion-service is recreated. Every proxy_pass must use the pool, and each location must speak
HTTP/1.1 without a Connection header, or nginx closes the upstream connection after each request.
"""

from __future__ import annotations

import re

from _repo import ROOT

CONF = (ROOT / "services" / "ingestion_edge" / "nginx.conf").read_text()
CODE = "\n".join(line.split("#", 1)[0] for line in CONF.splitlines())


def _upstream(name: str) -> str:
    m = re.search(r"upstream\s+" + name + r"\s*\{(.*?)\}", CODE, re.S)
    assert m, f"no upstream {name} block"
    return m.group(1)


def _locations() -> list[str]:
    return re.findall(r"location[^{]*\{((?:[^{}]|\{[^{}]*\})*)\}", CODE)


def test_the_pool_keeps_connections_alive_and_re_resolves():
    pool = _upstream("ingestion_service")
    assert re.search(r"^\s*zone\s+\S+\s+\S+;", pool, re.M), "resolve needs a shared zone"
    assert re.search(r"^\s*resolver\s+\S+", pool, re.M), "resolve needs a resolver"
    assert re.search(r"^\s*server\s+ingestion-service:\d+\s+resolve;", pool, re.M)
    assert re.search(r"^\s*keepalive\s+\d+;", pool, re.M)
    # Below uvicorn's 5 s default, so nginx never reuses a connection the service is closing.
    timeout = re.search(r"^\s*keepalive_timeout\s+(\d+)s;", pool, re.M)
    assert timeout and int(timeout.group(1)) < 5


def test_every_proxy_pass_uses_the_pool_over_http_1_1():
    proxied = [loc for loc in _locations() if "proxy_pass" in loc]
    assert proxied, "no proxied location found"
    for loc in proxied:
        assert re.search(r"proxy_pass\s+http://ingestion_service;", loc), loc
        assert re.search(r"proxy_http_version\s+1\.1;", loc), loc
        assert re.search(r'proxy_set_header\s+Connection\s+"";', loc), loc
