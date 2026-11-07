# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every Compose service's logs reach the log store.

The collector keeps only the containers its Docker filters select, and a service it skips fails
silently: its logs are simply absent when an alert or the infrastructure agent (ADR-0046 dec 2) reads
them. ingestion-service was such a service: it is on no network the collector selected, because only
its edge may reach it (ADR-0002 dec 3). Both sides are read here: the compose services, and what the
collector's filters select (by container name, or by network).
"""

from __future__ import annotations

import re

from _repo import load_yaml

COMPOSE = load_yaml("docker-compose.yml")
PROMTAIL = load_yaml("infrastructure/promtail/promtail-config.yml")


def _filters() -> list[dict[str, list[str]]]:
    """Each docker_sd_config's filters, as {filter name: values}; one config selects a container when
    every filter it names matches (Docker ANDs filters and ORs a filter's values)."""
    out = []
    for job in PROMTAIL["scrape_configs"]:
        for sd in job.get("docker_sd_configs", []):
            out.append({f["name"]: f["values"] for f in sd.get("filters", [])})
    assert out, "no Docker discovery in the collector's config — the guard is not looking"
    return out


def _networks(service: dict) -> set[str]:
    if service.get("network_mode"):
        return set()
    declared = COMPOSE.get("networks") or {}
    return {(declared.get(n) or {}).get("name", n) for n in (service.get("networks") or ["default"])}


def _selected(service: dict) -> bool:
    # The daemon names a container "/<name>"; the name filter is a regular expression.
    name = "/" + service.get("container_name", "")
    for filters in _filters():
        unknown = set(filters) - {"name", "network"}
        assert not unknown, f"the guard cannot read the collector's {sorted(unknown)} filters"
        by_name = "name" not in filters or any(re.search(v, name) for v in filters["name"])
        by_net = "network" not in filters or bool(_networks(service) & set(filters["network"]))
        if by_name and by_net and filters:
            return True
    return False


def _runs(service: dict) -> bool:
    # notebook-authoring and notebook-analytics only build the kernels' images (command `true`); the
    # kernels themselves are spawned on the kernel network.
    return service.get("command") != ["true"]


def test_every_compose_service_is_collected():
    services = {n: s for n, s in COMPOSE["services"].items() if _runs(s)}
    assert "ingestion-service" in services and len(services) > 30
    missing = sorted(n for n, s in services.items() if not _selected(s))
    assert not missing, f"the log collector skips {missing}"


def test_the_kernels_network_is_collected():
    kernels = COMPOSE["networks"]["notebook-kernels"]["name"]
    assert any(kernels in f.get("network", []) and "name" not in f for f in _filters()), (
        "spawned kernels carry no fixed name; only their network selects them"
    )


def test_ingestion_stays_reachable_only_from_its_edge():
    # Collected by name, not by joining the collector or anything else to the ingestion networks.
    nets = _networks(COMPOSE["services"]["ingestion-service"])
    others = sorted(n for n, s in COMPOSE["services"].items()
                    if n not in {"ingestion-service", "ingestion-edge", "kafka"} and _networks(s) & nets)
    assert not others, f"on the ingestion networks: {others}"
