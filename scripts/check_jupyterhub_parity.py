#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The hub and the single-user servers it spawns must run the same JupyterHub.

The hub pins JupyterHub in its requirements.txt, which a dependency bot can see. Each spawn image
pins it again inside a Dockerfile RUN line, which the bot cannot see. So a bot bump moves the hub
and leaves the spawn images behind. The build passes, the hub starts, and every spawned server runs
`jupyterhub-singleuser` from a different major than the hub that manages it.

Spawn images are found by GLOB, so a new `Dockerfile.<profile>` is checked without editing this file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HUB_REQUIREMENTS = "services/notebook_hub/requirements.txt"
SPAWN_DOCKERFILE_GLOB = "services/notebook_hub/Dockerfile.*"
PIN = re.compile(r"(?<![\w-])jupyterhub==([0-9][^\s\\]*)")


def find_mismatches(root: Path = REPO_ROOT) -> list[str]:
    hub = PIN.findall((root / HUB_REQUIREMENTS).read_text())
    if len(hub) != 1:
        return [f"{HUB_REQUIREMENTS}: expected one jupyterhub== pin, found {len(hub)}"]
    want = hub[0]
    problems = []
    images = sorted(root.glob(SPAWN_DOCKERFILE_GLOB))
    if not images:
        problems.append(f"no spawn images match {SPAWN_DOCKERFILE_GLOB}")
    for df in images:
        pins = PIN.findall(df.read_text())
        rel = df.relative_to(root)
        if not pins:
            problems.append(f"{rel}: no jupyterhub== pin (the hub runs {want})")
        problems += [f"{rel}: jupyterhub=={p}, hub runs {want}" for p in pins if p != want]
    return problems


def main() -> int:
    problems = find_mismatches()
    for p in problems:
        print(f"::error::{p}")
    if not problems:
        print("jupyterhub parity ok")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
