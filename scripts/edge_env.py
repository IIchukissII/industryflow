#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Print the frontend edge's environment as one deployment supplies it, as KEY=VALUE lines.

    edge_env.py compose      the frontend service's environment in docker-compose.yml
    edge_env.py helm [ARGS]  the frontend Deployment's env from `helm template` (ARGS passed on)

The edge renders its config from these at start (services/frontend/nginx.conf.template). CI feeds
each set to the built image and runs `nginx -t`, so both deployments' values are proven to produce
a config nginx accepts, and tests/config_guards checks each set is complete. Needs PyYAML; `helm`
mode needs helm on PATH.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CHART = ROOT / "deploy" / "helm" / "industryflow"
_INTERP = re.compile(r"\$\{(\w+)(?::-([^}]*))?\}")


def compose_env() -> dict[str, str]:
    service = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]["frontend"]
    # Compose interpolation with no .env: a `${VAR:-default}` takes its default.
    return {k: _INTERP.sub(lambda m: m.group(2) or "", str(v)) for k, v in service["environment"].items()}


def helm_env(extra: list[str]) -> dict[str, str]:
    out = subprocess.run(
        ["helm", "template", "if", str(CHART), "--set", "secrets.failOnPlaceholders=false", *extra],
        check=True, capture_output=True, text=True,
    ).stdout
    for doc in yaml.safe_load_all(out):
        if doc and doc.get("kind") == "Deployment" and doc["metadata"]["name"].endswith("-frontend"):
            container = doc["spec"]["template"]["spec"]["containers"][0]
            return {e["name"]: e["value"] for e in container.get("env", []) if "value" in e}
    raise SystemExit("no frontend Deployment in the render")


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in {"compose", "helm"}:
        print(__doc__, file=sys.stderr)
        return 2
    env = compose_env() if argv[1] == "compose" else helm_env(argv[2:])
    for key, value in env.items():
        print(f"{key}={value}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
