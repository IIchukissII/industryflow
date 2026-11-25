# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The single host holds only what the platform reads (ADR-0009 dec 6, ADR-0018 dec 6).

Three settings each kept memory the platform never used, and each fails silently when it drifts back:
ZooKeeper's heap on Compose was the image's committed 512M whatever its limit; a cAdvisor
`--disable_metrics` list replaced cAdvisor's own default list and turned its TCP, UDP and process
series back on; and Grafana installed and loaded app plugins no dashboard uses.
"""
from __future__ import annotations


import yaml
from _repo import ROOT

COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]


def _env(service: str) -> dict:
    env = COMPOSE[service].get("environment", {})
    return dict(e.split("=", 1) for e in env) if isinstance(env, list) else env


def test_zookeeper_heap_is_derived_not_the_images_default():
    """The image commits a 512M heap at start whatever its limit; the heap follows the budget instead."""
    assert _env("zookeeper").get("KAFKA_HEAP_OPTS") == "${ZOOKEEPER_HEAP_OPTS:?run scripts/host-memory.py}"


def test_cadvisor_collects_an_allowlist_of_metric_groups():
    args = [str(a) for a in COMPOSE["cadvisor"]["command"]]
    assert not [a for a in args if a.startswith("--disable_metrics")], (
        "--disable_metrics replaces cAdvisor's default list and re-enables what it leaves out")
    allowed = [a.split("=", 1)[1].split(",") for a in args if a.startswith("--enable_metrics=")]
    assert allowed == [["cpu", "memory", "network", "oom_event"]]


def test_grafana_loads_no_plugins_from_its_data_volume():
    env = _env("grafana")
    assert env.get("GF_PLUGINS_PREINSTALL_DISABLED") == "true"
    plugins = env.get("GF_PATHS_PLUGINS", "")
    data = [v.split(":", 1)[1] for v in COMPOSE["grafana"]["volumes"] if v.startswith("grafana-data:")][0]
    assert plugins and not plugins.startswith(data.rstrip("/") + "/")
