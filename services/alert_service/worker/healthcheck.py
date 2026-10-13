# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Liveness check for the alert worker (compose healthcheck and the Helm exec livenessProbe).

The worker has no HTTP port, and "PID 1 is still python" says nothing about whether it consumes:
a consumer loop can end or hang while the process stays up. The loop touches HEARTBEAT_FILE on
every poll, so a stale or missing file is exactly "nothing is consuming". Exit 0 when fresh, 1
otherwise.
"""
import os
import sys
import time

from config import config


def heartbeat_age(path: str, now: float) -> float:
    """Seconds since the heartbeat file was touched; infinity when it does not exist."""
    try:
        return now - os.path.getmtime(path)
    except OSError:
        return float("inf")


def main() -> int:
    path = config.HEARTBEAT_FILE
    max_age = config.HEARTBEAT_MAX_AGE_SECONDS
    age = heartbeat_age(path, time.time())
    if age <= max_age:
        return 0
    print(f"alert worker heartbeat stale: {path} age {age:.0f}s > {max_age:.0f}s", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
