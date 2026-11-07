#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The services that mount a changed repository file as a single-file bind mount.

    docker compose config --format json | scripts/compose_file_mounts.py <project-dir> <changed-list>

A single-file mount keeps the file the checkout replaced until its container is recreated, so a
deploy recreates every service that mounts a file it changed (scripts/box-deploy.sh). <changed-list>
holds one repository-relative path per line. Prints one service name per line.
"""

from __future__ import annotations

import json
import os
import sys


def stale_services(config: dict, project: str, changed: set[str]) -> list[str]:
    project = os.path.realpath(project)
    out = []
    for name, service in sorted(config.get("services", {}).items()):
        for volume in service.get("volumes", []):
            if volume.get("type") != "bind":
                continue
            source = os.path.realpath(volume.get("source", ""))
            if not source.startswith(project + os.sep) or not os.path.isfile(source):
                continue
            if os.path.relpath(source, project) in changed:
                out.append(name)
                break
    return out


def main(argv: list[str]) -> int:
    project, changed_list = argv[1], argv[2]
    with open(changed_list) as f:
        changed = {line.strip() for line in f if line.strip()}
    for name in stale_services(json.load(sys.stdin), project, changed):
        print(name)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
