# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reclaim's Docker backend against a real Docker daemon (ADR-0020 dec 6, ADR-0026 dec 11).

A deleted user's work volume is named as DockerSpawner names it (test_reclaim.py proves the name);
here the daemon answers: a volume a container still mounts is removed with that container, a removed
volume no longer remains, and another user's volume is left alone.

Needs a Docker daemon the docker SDK reaches from the environment. Skips without one, unless
IF_REQUIRE_LIVE_STACK=1, which makes the skip a failure.
"""
import os
import re
import sys
import uuid
from pathlib import Path

import pytest

import docker

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import reclaim  # noqa: E402

pytestmark = pytest.mark.integration

TEMPLATE = "industryflow-nb-{username}"
COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.yml"
# A small image the platform already pins, so the proof pulls nothing new.
IMAGE = re.search(r"^\s+image: (alpine:[^\s]+)$", COMPOSE.read_text(), re.M).group(1)


@pytest.fixture(scope="module")
def client():
    try:
        c = docker.from_env()
        c.ping()
    except Exception as exc:  # noqa: BLE001
        if os.getenv("IF_REQUIRE_LIVE_STACK") == "1":
            pytest.fail(f"IF_REQUIRE_LIVE_STACK=1 but no Docker daemon: {exc}", pytrace=False)
        pytest.skip(f"no Docker daemon: {exc}")
    c.images.pull(IMAGE)
    return c


@pytest.fixture
def volumes(client):
    """A deleted user's volume, held by a stopped container as an exited server's is, and a neighbour's."""
    gone, kept = str(uuid.uuid4()), str(uuid.uuid4())
    backend = reclaim.DockerVolumes(TEMPLATE, client=client)
    for user in (gone, kept):
        client.volumes.create(backend.name(user))
    held = client.containers.create(IMAGE, "true", volumes={backend.name(gone): {"bind": "/home/jovyan/work"}})
    yield backend, gone, kept
    for user in (gone, kept):
        for c in client.containers.list(all=True, filters={"volume": backend.name(user)}):
            c.remove(force=True)
        try:
            client.volumes.get(backend.name(user)).remove()
        except docker.errors.NotFound:
            pass
    try:
        held.remove(force=True)
    except docker.errors.NotFound:
        pass


def test_a_mounted_volume_is_removed_with_its_container_and_no_longer_remains(client, volumes):
    backend, gone, kept = volumes
    assert backend.remains(gone) is True
    backend.remove(gone)
    assert backend.remains(gone) is False
    assert client.containers.list(all=True, filters={"volume": backend.name(gone)}) == []


def test_another_users_volume_is_left_alone(volumes):
    backend, gone, kept = volumes
    backend.remove(gone)
    assert backend.remains(kept) is True


def test_a_volume_that_was_never_created_does_not_remain(volumes):
    backend, _, _ = volumes
    assert backend.remains(str(uuid.uuid4())) is False
