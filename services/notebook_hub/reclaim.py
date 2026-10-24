# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The hub's reclaim of a deleted tenant's notebook volumes (ADR-0020 dec 6, ADR-0033 dec 4).

A hub-managed service. It reads the deletions still awaiting a reclaim from the platform's deletion
record, deletes each of the tenant's users from the hub, and records the notebook_volumes store once
every user is gone and no volume of theirs remains. Deleting a hub user stops its servers and has its
spawner remove the user's volume (``delete_forever``), so the volume goes through the machinery that
created it. The hub's answer is not proof: JupyterHub logs a failed ``delete_forever`` and still
deletes the user. So the spawner backend is asked by the name the spawner gives the volume, a volume
that remains is removed here, and a tenant whose volume still remains is left for the next pass. A
user the hub never saw never authored, so has no volume.

Its hub token can delete users and nothing else; its database identity can list pending reclaims and
record this one store and nothing else (27-notebook-volume-reclaim.sql); it reaches the spawner
backend with the hub's own access. All stay inside the hub.

Env: JUPYTERHUB_API_URL, JUPYTERHUB_API_TOKEN (set by the hub), NOTEBOOK_RECLAIM_DB_* (the database
and notebook_hub_reclaim_user), NOTEBOOK_RECLAIM_SPAWNER and NOTEBOOK_RECLAIM_VOLUME_TEMPLATE (the
hub's spawner and volume name), NOTEBOOK_RECLAIM_EVERY_SECONDS.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Iterable, Protocol

logger = logging.getLogger("notebook_reclaim")

# The outcome of deleting one hub user.
DELETED, ABSENT, BUSY = "deleted", "absent", "busy"


class Volumes(Protocol):
    """The spawner backend's view of a user's work volume."""

    def remains(self, user_id: str) -> bool: ...
    def remove(self, user_id: str) -> None: ...


def reclaim_once(pending: Iterable, delete_user: Callable[[str], str], volumes: Volumes,
                 record: Callable[[str, dict], None]) -> dict:
    """One pass: company_id -> what was done. A tenant with a user still stopping, or a volume still
    in the backend, is left for the next pass."""
    done = {}
    for company_id, user_ids in pending:
        outcomes = {}
        for user_id in user_ids:
            try:
                outcomes[str(user_id)] = delete_user(str(user_id))
            except Exception:  # noqa: BLE001 - one user's failure leaves the tenant for the next pass
                logger.exception("tenant %s: user %s not deleted from the hub", company_id, user_id)
                outcomes[str(user_id)] = BUSY
        if BUSY in outcomes.values():
            logger.info("tenant %s: not every user is deleted yet; the next pass resumes", company_id)
            continue
        left = _remaining(volumes, outcomes)
        for user_id in left:
            try:
                volumes.remove(user_id)
            except Exception:  # noqa: BLE001 - checked again below
                logger.exception("tenant %s: volume of user %s not removed", company_id, user_id)
        still = _remaining(volumes, left)
        if still:
            logger.warning("tenant %s: %d volume(s) remain after the hub deleted their users; the next "
                           "pass retries", company_id, len(still))
            continue
        detail = {"hub_users_deleted": sum(o == DELETED for o in outcomes.values()),
                  "users_never_in_hub": sum(o == ABSENT for o in outcomes.values()),
                  "volumes_removed_after_the_hub": len(left)}
        record(str(company_id), detail)
        logger.info("tenant %s: notebook volumes reclaimed %s", company_id, detail)
        done[str(company_id)] = detail
    return done


def _remaining(volumes: Volumes, user_ids: Iterable[str]) -> list:
    left = []
    for user_id in user_ids:
        try:
            if volumes.remains(user_id):
                left.append(user_id)
        except Exception:  # noqa: BLE001 - an unanswered question is not an empty backend
            logger.exception("volume of user %s could not be checked", user_id)
            left.append(user_id)
    return left


# --------------------------------------------------------------------- live adapters (hub-bound)

class DockerVolumes:
    """Named volumes, by the name DockerSpawner gives them ({username} is its escaped name)."""

    def __init__(self, template: str, client=None):
        if client is None:
            import docker
            client = docker.from_env()
        self._client, self._template = client, template

    def name(self, user_id: str) -> str:
        from dockerspawner import DockerSpawner
        return self._template.format(username=DockerSpawner._escape(user_id))

    def remains(self, user_id: str) -> bool:
        from docker.errors import NotFound
        try:
            self._client.volumes.get(self.name(user_id))
        except NotFound:
            return False
        return True

    def remove(self, user_id: str) -> None:
        name = self.name(user_id)
        # Docker refuses to remove a volume a container still mounts; that container's user is deleted.
        for held in self._client.containers.list(all=True, filters={"volume": name}):
            held.remove(force=True)
        self._client.volumes.get(name).remove()


class KubeVolumes:
    """Claims in the hub's namespace, by the name KubeSpawner gives them ({username} as a safe slug).
    Through kubernetes_asyncio, the client KubeSpawner itself uses."""

    _NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"

    def __init__(self, template: str, api_factory=None, namespace: str | None = None):
        if namespace is None:
            with open(self._NAMESPACE_FILE) as f:
                namespace = f.read().strip()
        self._api_factory = api_factory or self._incluster_api
        self._template, self._namespace = template, namespace

    @staticmethod
    async def _incluster_api():  # pragma: no cover - needs a cluster
        from kubernetes_asyncio import client, config
        config.load_incluster_config()
        return client.CoreV1Api(client.ApiClient())

    def name(self, user_id: str) -> str:
        from kubespawner.slugs import safe_slug
        return self._template.format(username=safe_slug(user_id, max_length=48))

    def _call(self, method: str, user_id: str):
        async def call():
            api = await self._api_factory()
            try:
                return await getattr(api, method)(self.name(user_id), self._namespace)
            finally:
                await api.api_client.close()
        return asyncio.run(call())

    def remains(self, user_id: str) -> bool:
        from kubernetes_asyncio.client.exceptions import ApiException
        try:
            self._call("read_namespaced_persistent_volume_claim", user_id)
        except ApiException as e:
            if e.status == 404:
                return False
            raise
        return True  # a claim being deleted still holds the data until it is gone

    def remove(self, user_id: str) -> None:
        from kubernetes_asyncio.client.exceptions import ApiException
        try:
            self._call("delete_namespaced_persistent_volume_claim", user_id)
        except ApiException as e:
            if e.status != 404:
                raise


def volumes_for(spawner: str, template: str) -> Volumes:  # pragma: no cover - needs the backend
    return DockerVolumes(template) if spawner == "docker" else KubeVolumes(template)


def _delete_from_hub(api_url: str, token: str):
    def delete_user(user_id: str) -> str:
        request = urllib.request.Request(f"{api_url.rstrip('/')}/users/{urllib.parse.quote(user_id)}",
                                         method="DELETE", headers={"Authorization": f"token {token}"})
        try:
            with urllib.request.urlopen(request, timeout=300):
                return DELETED
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return ABSENT
            if e.code == 400:  # its server is still stopping
                return BUSY
            raise
    return delete_user


def _db():  # pragma: no cover - needs the database
    import psycopg2
    import psycopg2.extras

    # uuid and uuid[] as UUIDs: without it psycopg2 hands an array back as its text, '{...}'.
    psycopg2.extras.register_uuid()
    return psycopg2.connect(
        host=os.environ["NOTEBOOK_RECLAIM_DB_HOST"],
        port=os.environ.get("NOTEBOOK_RECLAIM_DB_PORT", "5432"),
        dbname=os.environ.get("NOTEBOOK_RECLAIM_DB_NAME", "industryflow"),
        user=os.environ.get("NOTEBOOK_RECLAIM_DB_USER", "notebook_hub_reclaim_user"),
        password=os.environ["NOTEBOOK_RECLAIM_DB_PASSWORD"],
        sslmode=os.environ.get("NOTEBOOK_RECLAIM_DB_SSLMODE", "verify-full"),
        sslrootcert=os.environ.get("NOTEBOOK_RECLAIM_DB_SSLROOTCERT", "/etc/ssl/industryflow/ca.crt"),
        connect_timeout=10,
    )


def main() -> int:  # pragma: no cover - the hub runs it
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    every = int(os.environ.get("NOTEBOOK_RECLAIM_EVERY_SECONDS", "300"))
    delete_user = _delete_from_hub(os.environ["JUPYTERHUB_API_URL"], os.environ["JUPYTERHUB_API_TOKEN"])
    volumes = volumes_for(os.environ["NOTEBOOK_RECLAIM_SPAWNER"], os.environ["NOTEBOOK_RECLAIM_VOLUME_TEMPLATE"])
    while True:
        conn = None
        try:
            conn = _db()
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT * FROM public.pending_notebook_volume_reclaims()")
                pending = cur.fetchall()

                def record(company_id, detail):
                    cur.execute("SELECT public.record_notebook_volumes_reclaimed(%s, %s::jsonb)",
                                (company_id, json.dumps(detail)))

                reclaim_once(pending, delete_user, volumes, record)
        except Exception:  # noqa: BLE001 - a failed pass is retried on the next
            logger.exception("reclaim pass failed")
        finally:
            if conn is not None:
                conn.close()
        time.sleep(every)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
