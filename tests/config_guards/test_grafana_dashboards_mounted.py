# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Every dashboard in the provisioning directory is mounted into Compose's Grafana.

The chart ships the directory by glob, but Compose mounts each dashboard file on its own: a new JSON
file is on Kubernetes and silently missing on a single host.
"""

from __future__ import annotations

from _repo import ROOT, load_yaml

PROVISIONING = ROOT / "infrastructure" / "grafana" / "provisioning"


def test_every_dashboard_file_is_mounted():
    mounts = load_yaml("docker-compose.yml")["services"]["grafana"]["volumes"]
    mounted = {m.split(":")[0].removeprefix("./") for m in mounts if isinstance(m, str)}
    files = {str(p.relative_to(ROOT)) for p in PROVISIONING.glob("*.json")}
    assert len(files) >= 3
    missing = sorted(files - mounted)
    assert not missing, f"dashboards Compose's Grafana never loads: {missing}"
