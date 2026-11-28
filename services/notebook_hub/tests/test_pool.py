# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The notebook pool's limits are applied in the host's namespaces before the hub starts, and read back
(ADR-0018 dec 6, ADR-0050 dec 5). The shell it runs is proved on a real host by the compose smoke gate."""
from __future__ import annotations

import pathlib
import sys

import pytest
from docker.errors import ContainerError, ImageNotFound

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import pool  # noqa: E402

ENV = {"NOTEBOOK_CGROUP_PARENT": "industryflow-notebooks.slice", "NOTEBOOK_MEMORY_SHARE_MIB": "1400",
       "NOTEBOOK_SWAP_SHARE_MIB": "5000", "NOTEBOOK_POOL_IMAGE": "alpine:3.24@sha256:" + "0" * 64}


class _Images:
    def __init__(self, present):
        self.present, self.pulled = present, []

    def get(self, image):
        if not self.present:
            raise ImageNotFound(image)

    def pull(self, image):
        self.pulled.append(image)


class _Containers:
    def __init__(self, fail=False):
        self.fail, self.runs = fail, []

    def run(self, image, command, **kwargs):
        self.runs.append((image, command, kwargs))
        if self.fail:
            raise ContainerError("pool", 1, command, image, b"notebook pool x: memory max, swap max")
        return b"notebook pool industryflow-notebooks.slice: 1400 MiB memory, 5000 MiB swap\n"


class _Client:
    def __init__(self, present=True, fail=False):
        self.images, self.containers = _Images(present), _Containers(fail)


def test_the_limits_are_set_in_the_hosts_namespaces_from_the_budget():
    client = _Client(present=False)
    out = pool.apply(client, ENV)
    assert "1400 MiB memory" in out and client.images.pulled == [ENV["NOTEBOOK_POOL_IMAGE"]]
    (image, command, kwargs), = client.containers.runs
    assert image == ENV["NOTEBOOK_POOL_IMAGE"] and command[:6] == ["nsenter", "-t", "1", "-m", "--", "sh"]
    assert kwargs["privileged"] and kwargs["pid_mode"] == "host" and kwargs["network_mode"] == "none"
    assert kwargs["environment"] == {"POOL": "industryflow-notebooks.slice", "POOL_MIB": "1400", "POOL_SWAP_MIB": "5000"}
    assert kwargs["remove"]


def test_limits_that_do_not_hold_stop_the_hub_naming_them():
    with pytest.raises(SystemExit, match="memory max, swap max"):
        pool.apply(_Client(fail=True), ENV)


@pytest.mark.parametrize("name", sorted(ENV))
def test_without_its_part_of_the_budget_the_hub_does_not_start(name):
    for missing in ("", "x") if name.endswith("_MIB") else ("",):
        with pytest.raises(SystemExit, match="notebook pool"):
            pool.apply(_Client(), {**ENV, name: missing})


def test_the_script_reads_the_limits_back_against_the_budget():
    """Both drivers set the limits and compare what the host holds with the budget's bytes."""
    assert 'systemctl set-property "$POOL" MemoryMax=' in pool.SCRIPT and "--runtime" not in pool.SCRIPT
    assert 'echo "$want_mem" > "$d/memory.max"' in pool.SCRIPT
    assert '[ "$mem" = "$want_mem" ] && [ "$swap" = "$want_swap" ]' in pool.SCRIPT
