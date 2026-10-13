# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""`.env.example` is the operator's map of what can be configured; a variable nothing reads is a
setting that silently does nothing (SPARK_DRIVER_MEMORY was one, REDIS_PASSWORD another)."""

from __future__ import annotations

import re

from _repo import ROOT

ENV = (ROOT / ".env.example").read_text()
COMPOSE = (ROOT / "docker-compose.yml").read_text()

# Read by something other than compose interpolation: scripts/seed_users.py builds these names; the
# suffixes are read from the script itself, so a new one it reads is not mistaken for dead.
SEED = (ROOT / "scripts" / "seed_users.py").read_text()
SCRIPT_READ = re.compile(r"^ADMIN_USER_\d+_(%s)$" % "|".join(sorted(set(re.findall(r"ADMIN_USER_\{i\}_(\w+)", SEED)))))


def test_every_variable_is_read_by_compose_or_a_named_script():
    keys = re.findall(r"^([A-Z0-9_]+)=", ENV, re.M)
    interpolated = set(re.findall(r"\$\{(\w+)", COMPOSE))
    dead = [k for k in keys if k not in interpolated and not SCRIPT_READ.match(k)]
    assert not dead, f".env.example variables nothing reads: {dead}"
    assert "ADMIN_USER_{i}_" in SEED


def test_no_private_network_address_is_committed():
    private = re.compile(r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b")
    for rel in (".env.example", "docker-compose.yml", "deploy/helm/industryflow/values.yaml"):
        hits = private.findall((ROOT / rel).read_text())
        assert not hits, f"{rel} carries a private address {hits}; use a placeholder such as <host-ip>"
