# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reclaim records a tenant's volumes only once every one of its users is gone from the hub and
the spawner backend holds no volume of theirs."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import reclaim  # noqa: E402

A, B = "0b2f503a-6e59-4f4f-b0cd-b82547a20cf2", "11111111-2222-3333-4444-555555555555"


class FakeVolumes:
    """The spawner backend. ``kept`` volumes survive the hub's delete (its error is swallowed);
    ``stuck`` ones survive removal here too; ``unreadable`` ones cannot be checked."""

    def __init__(self, kept=(), stuck=(), unreadable=()):
        self.present, self.stuck, self.unreadable = set(kept) | set(stuck), set(stuck), set(unreadable)
        self.removed = []

    def remains(self, user_id):
        if user_id in self.unreadable:
            raise ConnectionError("backend unreachable")
        return user_id in self.present

    def remove(self, user_id):
        if user_id in self.stuck:
            raise RuntimeError("409 volume is in use")
        self.present.discard(user_id)
        self.removed.append(user_id)


def _run(pending, outcomes, volumes=None):
    deleted, recorded = [], []

    def delete_user(user_id):
        deleted.append(user_id)
        outcome = outcomes.get(user_id, reclaim.DELETED)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    done = reclaim.reclaim_once(pending, delete_user, volumes or FakeVolumes(),
                                lambda c, d: recorded.append((c, d)))
    return deleted, recorded, done


def test_every_user_is_deleted_then_the_store_recorded():
    deleted, recorded, _ = _run([(A, ["u1", "u2", "u3"])], {"u3": reclaim.ABSENT})
    assert deleted == ["u1", "u2", "u3"]
    assert recorded == [(A, {"hub_users_deleted": 2, "users_never_in_hub": 1, "volumes_removed_after_the_hub": 0})]


def test_a_tenant_without_users_is_recorded_as_nothing_to_reclaim():
    _, recorded, _ = _run([(A, [])], {})
    assert recorded == [(A, {"hub_users_deleted": 0, "users_never_in_hub": 0, "volumes_removed_after_the_hub": 0})]


def test_a_user_still_stopping_or_failing_leaves_only_its_tenant_for_the_next_pass():
    _, recorded, done = _run([(A, ["u1", "u2"]), (B, ["u3"])],
                             {"u1": reclaim.BUSY, "u2": RuntimeError("docker unreachable")})
    assert [c for c, _ in recorded] == [B] and list(done) == [B]


def test_a_volume_the_hub_left_behind_is_removed_before_the_store_is_recorded():
    # JupyterHub logs a failed delete_forever and still answers 204 (box e2e, 2026-10-24).
    volumes = FakeVolumes(kept=["u2"])
    _, recorded, _ = _run([(A, ["u1", "u2"])], {}, volumes)
    assert volumes.removed == ["u2"] and volumes.present == set()
    assert recorded == [(A, {"hub_users_deleted": 2, "users_never_in_hub": 0, "volumes_removed_after_the_hub": 1})]


def test_a_volume_that_cannot_be_removed_leaves_its_tenant_pending():
    _, recorded, done = _run([(A, ["u1"]), (B, ["u3"])], {}, FakeVolumes(stuck=["u1"]))
    assert [c for c, _ in recorded] == [B] and list(done) == [B]


def test_a_backend_that_cannot_answer_is_not_an_empty_one():
    _, recorded, _ = _run([(A, ["u1"])], {}, FakeVolumes(unreadable=["u1"]))
    assert recorded == []


# --- the backend is asked by the name the spawner itself gives the volume ---

USERNAMES = ["a86cdca5-e447-4795-84b7-bf42c8a4c223", "Mixed.Case_user@example.com", "x" * 70]
TEMPLATE = "industryflow-nb-{username}"


def _user(name):
    from types import SimpleNamespace
    return SimpleNamespace(name=name, id=1, url=f"/user/{name}/")


@pytest.mark.parametrize("username", USERNAMES)
def test_docker_names_the_volume_as_dockerspawner_does(username):
    from dockerspawner import DockerSpawner

    spawner = DockerSpawner(user=_user(username), _mock=True)
    assert reclaim.DockerVolumes(TEMPLATE, client=object()).name(username) == \
        spawner.format_volume_name(TEMPLATE, spawner)


@pytest.mark.parametrize("username", USERNAMES)
def test_kube_names_the_claim_as_kubespawner_does(username, monkeypatch):
    import kubespawner.spawner
    from kubespawner import KubeSpawner

    # Built without a cluster: KubeSpawner loads a kube config and an API client when constructed.
    monkeypatch.setattr(kubespawner.spawner, "load_config", lambda **kwargs: None)
    monkeypatch.setattr(kubespawner.spawner, "shared_client", lambda name: None)

    async def expected():
        spawner = KubeSpawner(user=_user(username), _mock=True, pvc_name_template=TEMPLATE)
        return spawner._expand_user_properties(spawner.pvc_name_template)

    assert reclaim.KubeVolumes(TEMPLATE, namespace="ns").name(username) == asyncio.run(expected())


def test_docker_removes_the_containers_still_mounting_the_volume_first():
    from docker.errors import NotFound

    class Container:
        def __init__(self, store, cid):
            self.store, self.cid = store, cid

        def remove(self, force=False):
            assert force
            self.store.mounts.pop(self.cid)

    class Volume:
        def __init__(self, store, name):
            self.store, self.name = store, name

        def remove(self):
            if self.name in self.store.mounts.values():
                raise RuntimeError("409 volume is in use")
            self.store.volumes.discard(self.name)

    class Client:
        def __init__(self, name):
            self.volumes_set, self.mounts = {name}, {"c0ffee": name}
            self.volumes, self.containers = self, self

        def get(self, name):
            if name not in self.volumes_set:
                raise NotFound("no such volume")
            return Volume(self, name)

        def list(self, all=False, filters=None):
            return [Container(self, cid) for cid, v in self.mounts.items() if v == filters["volume"]]

        def discard(self, name):
            self.volumes_set.discard(name)

    client = Client(reclaim.DockerVolumes(TEMPLATE, client=object()).name(USERNAMES[0]))
    v = reclaim.DockerVolumes(TEMPLATE, client=client)
    assert v.remains(USERNAMES[0]) is True
    v.remove(USERNAMES[0])
    assert client.mounts == {} and v.remains(USERNAMES[0]) is False


def test_kube_counts_a_claim_until_it_is_gone():
    from kubernetes_asyncio.client.exceptions import ApiException

    claims = {"industryflow-nb-u1": "Bound"}
    calls = []

    class Api:
        class api_client:
            @staticmethod
            async def close():
                pass

        async def read_namespaced_persistent_volume_claim(self, name, namespace):
            calls.append(("read", name, namespace))
            if name not in claims:
                raise ApiException(status=404)
            return claims[name]

        async def delete_namespaced_persistent_volume_claim(self, name, namespace):
            calls.append(("delete", name, namespace))
            if name not in claims:
                raise ApiException(status=404)
            claims[name] = "Terminating"  # a claim still mounted is deleted only once released

    async def api():
        return Api()

    v = reclaim.KubeVolumes(TEMPLATE, api_factory=api, namespace="industryflow")
    v.remove("u1")
    assert v.remains("u1") is True     # terminating still holds the data
    claims.clear()
    assert v.remains("u1") is False
    v.remove("u1")                     # already gone
    assert calls[0] == ("delete", "industryflow-nb-u1", "industryflow")
