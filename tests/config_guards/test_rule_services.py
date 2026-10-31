# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every platform rule names the services behind it, and each name is a real service.

The infrastructure agent reads an alert's `services` annotation to choose whose logs to read
(ADR-0046 dec 2). A rule without one, or with a name no service has, explains its alert from metrics
alone and nothing says so.
"""

from __future__ import annotations

from _repo import load_yaml

RULES = [r for g in load_yaml("infrastructure/prometheus/rules/platform.yml")["groups"] for r in g["rules"]]
SERVICES = set(load_yaml("docker-compose.yml")["services"])


def test_the_guard_reads_both_sides():
    assert len(RULES) >= 8 and "api-gateway" in SERVICES


def test_every_rule_names_its_services():
    missing = [r["alert"] for r in RULES if not r.get("annotations", {}).get("services", "").strip()]
    assert not missing, f"rules naming no services: {missing}"


def test_every_named_service_exists():
    unknown = sorted(
        f"{r['alert']}: {name}"
        for r in RULES
        for name in (n.strip() for n in r["annotations"]["services"].split(","))
        if name and "{{" not in name and name not in SERVICES
    )
    assert not unknown, f"services no compose service is called: {unknown}"
