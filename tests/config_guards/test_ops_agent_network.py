# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""On Compose the infrastructure agent reaches only the monitoring stack (ADR-0046 dec 7).

Its reach is the metrics store, the log store, the alert router and the model endpoint. A container on
a shared network reaches every other container on it, so the agent's networks may hold only those
three; the model endpoint is outside the host, which a non-internal network routes to.
"""

from __future__ import annotations

from urllib.parse import urlparse

from _repo import load_yaml

COMPOSE = load_yaml("docker-compose.yml")
SERVICES = COMPOSE["services"]
REACH = {"prometheus", "loki", "alertmanager"}


def _networks(service: str) -> set[str]:
    nets = SERVICES[service].get("networks") or ["default"]
    return set(nets)


def _neighbours(service: str) -> set[str]:
    mine = _networks(service)
    return {name for name in SERVICES if name != service and _networks(name) & mine}


def test_the_agent_shares_a_network_only_with_the_monitoring_stack():
    extra = _neighbours("ops-agent") - REACH
    assert not extra, f"the agent shares a network with {sorted(extra)} (ADR-0046 dec 7)"


def test_the_agent_reaches_every_store_it_reads():
    missing = REACH - _neighbours("ops-agent")
    assert not missing, f"the agent cannot reach {sorted(missing)}"
    env = SERVICES["ops-agent"].get("environment", {})
    for var in ("OPS_AGENT_PROMETHEUS_URL", "OPS_AGENT_LOKI_URL", "OPS_AGENT_ALERTMANAGER_URL"):
        if var in env:
            host = urlparse(str(env[var]).split(":-", 1)[-1].rstrip("}")).hostname
            assert host in REACH, f"{var} names {host}, which is not on the agent's network"


def test_the_monitoring_stack_stays_on_the_platform_network():
    # Prometheus scrapes, Loki receives the shipped logs, Alertmanager takes Prometheus's alerts.
    platform = _networks("timescaledb")
    for name in REACH:
        assert _networks(name) & platform, f"{name} left the platform network"


def test_the_agents_logs_are_still_collected():
    # The log collector keeps only containers on the networks it lists; the agent's own logs are
    # evidence of the agent itself.
    promtail = load_yaml("infrastructure/promtail/promtail-config.yml")
    listed = {
        v for job in promtail["scrape_configs"] for sd in job.get("docker_sd_configs", [])
        for f in sd.get("filters", []) if f["name"] == "network" for v in f["values"]
    }
    names = {COMPOSE["networks"][n].get("name", n) for n in _networks("ops-agent")}
    assert names & listed, f"the log collector skips the agent: {sorted(names)} not in {sorted(listed)}"


def test_the_agent_network_has_a_route_to_a_hosted_model():
    for net in _networks("ops-agent"):
        assert not (COMPOSE.get("networks") or {}).get(net, {}).get("internal"), (
            f"network {net} is internal: a hosted model endpoint is unreachable"
        )
