# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The notebook pool's limits on a Docker host (ADR-0018 dec 6, ADR-0050 dec 5), applied before the hub starts.

    python pool.py

Every session runs in one cgroup, the pool, whose memory and swap limits are what the platform leaves of the
host (scripts/host-memory.py): together the sessions never take a platform service's memory. The hub's
entrypoint runs this on every start, a reboot's included, so no session is admitted into a pool without its
limits. It sets them in the host's namespaces through the hub's Docker socket, from a privileged one-shot
container with the host's PIDs: under the systemd cgroup driver as the slice's properties (systemd rewrites
the limits of a slice it does not own), under cgroupfs in the cgroup itself. It then reads them back; a pool
whose limits are not the budget's stops the hub.

Reads NOTEBOOK_CGROUP_PARENT, NOTEBOOK_MEMORY_SHARE_MIB, NOTEBOOK_SWAP_SHARE_MIB and NOTEBOOK_POOL_IMAGE.
"""
from __future__ import annotations

import os
import sys

# Runs in the host's mount namespace: the host's systemctl, or its cgroup file system. Reads the limits back
# and fails unless they are the budget's, in bytes.
SCRIPT = r"""
set -eu
want_mem=$((POOL_MIB * 1048576)); want_swap=$((POOL_SWAP_MIB * 1048576))
case "$POOL" in
  *.slice)
    systemctl set-property "$POOL" MemoryMax="${POOL_MIB}M" MemorySwapMax="${POOL_SWAP_MIB}M"
    systemctl start "$POOL"
    mem=$(systemctl show -P MemoryMax "$POOL"); swap=$(systemctl show -P MemorySwapMax "$POOL") ;;
  /*)
    d="/sys/fs/cgroup$POOL"; mkdir -p "$d"
    echo "$want_mem" > "$d/memory.max"; echo "$want_swap" > "$d/memory.swap.max"
    mem=$(cat "$d/memory.max"); swap=$(cat "$d/memory.swap.max") ;;
  *) echo "notebook pool: $POOL is neither a slice nor a path" >&2; exit 1 ;;
esac
[ "$mem" = "$want_mem" ] && [ "$swap" = "$want_swap" ] || {
  echo "notebook pool $POOL: memory $mem, swap $swap; the budget's are $want_mem and $want_swap" >&2; exit 1; }
echo "notebook pool $POOL: $POOL_MIB MiB memory, $POOL_SWAP_MIB MiB swap"
"""


def _mib(env, name: str) -> int:
    raw = env.get(name, "").strip()
    if not raw.isdigit():
        raise SystemExit(f"notebook pool: {name} must be a number of MiB: run scripts/host-memory.py")
    return int(raw)


def apply(client, env) -> str:
    """Set the pool's limits in the host's namespaces and read them back; ``SystemExit`` unless they hold."""
    parent = env.get("NOTEBOOK_CGROUP_PARENT", "").strip()
    image = env.get("NOTEBOOK_POOL_IMAGE", "").strip()
    if not parent or not image:
        raise SystemExit("notebook pool: NOTEBOOK_CGROUP_PARENT and NOTEBOOK_POOL_IMAGE must be set")
    values = {"POOL": parent, "POOL_MIB": str(_mib(env, "NOTEBOOK_MEMORY_SHARE_MIB")),
              "POOL_SWAP_MIB": str(_mib(env, "NOTEBOOK_SWAP_SHARE_MIB"))}
    from docker.errors import ContainerError, ImageNotFound

    try:
        client.images.get(image)
    except ImageNotFound:
        client.images.pull(image)
    try:
        out = client.containers.run(image, ["nsenter", "-t", "1", "-m", "--", "sh", "-c", SCRIPT],
                                    environment=values, privileged=True, pid_mode="host", network_mode="none",
                                    remove=True, stdout=True, stderr=True)
    except ContainerError as e:
        raise SystemExit(f"notebook pool: {(e.stderr or b'').decode(errors='replace').strip() or e}") from None
    return out.decode(errors="replace").strip()


def main() -> int:
    import docker

    print(apply(docker.from_env(), os.environ), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
