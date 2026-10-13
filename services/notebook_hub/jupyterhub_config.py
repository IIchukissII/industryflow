# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JupyterHub configuration for the IndustryFlow notebook hub (ADR-0011/0012/0014/0018).

The spawner is a DEPLOYMENT PROFILE (ADR-0018): set ``NOTEBOOK_SPAWNER=kube`` for KubeSpawner
(per-user pod on Kubernetes) or ``NOTEBOOK_SPAWNER=docker`` for DockerSpawner (per-user container
on a Docker host / Compose). Everything ABOVE the spawner is identical and lives in the pure,
unit-tested ``identity.py`` + ``capabilities.py``:

  * SSO from the platform session via the trusted proxy (ADR-0014): the hub trusts the verified
    identity headers the proxy forwards and runs no login of its own.
  * Role-driven, enforced spawn profiles (ADR-0011 dec 5): profile derived from the verified role.
  * Identity-only environment (ADR-0012 dec 5): the kernel gets its tenant/identity, never a data
    credential — only opaque, per-session, single-tenant capability handles minted here (ADR-0015).
  * Contained per-user environment (ADR-0011 dec 7): non-root, no privilege escalation, dropped
    capabilities, resource limits. Egress is a NetworkPolicy on k8s; on Compose it is a dedicated
    internal network (the documented non-parity, ADR-0018 dec 4).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import capabilities as cap  # noqa: E402
import identity as ident  # noqa: E402

# Which spawner profile this deployment runs (ADR-0018). Default kube preserves the original
# Kubernetes behaviour; the Compose deployment sets NOTEBOOK_SPAWNER=docker.
SPAWNER = os.environ.get("NOTEBOOK_SPAWNER", "kube").strip().lower()

# Capability lifetime (ADR-0015 dec 2). The store entry's lifetime is only the backstop: the hub
# refreshes a healthy server's handles on every spawner poll (Spawner.poll_interval, 30s by
# default) and deletes them when the server stops, is respawned, or its user logs out. So this
# bounds how long a handle outlives a hub that stopped looking after it, not how long a session
# may last. Config-owned.
CAPABILITY_TTL_SECONDS = int(os.environ.get("NOTEBOOK_CAPABILITY_TTL_SECONDS", "900"))
# How long a hub login is trusted before it is re-checked against the identity the SSO proxy
# forwards (ADR-0014): a different user, tenant or role behind the same hub cookie ends the login.
AUTH_REFRESH_SECONDS = int(os.environ.get("NOTEBOOK_AUTH_REFRESH_SECONDS", "60"))

# Per-user durable work storage (ADR-0020): a data scientist's notebooks persist at work/ across
# the ephemeral container; the rest of the home (incl the read-only examples/ baked into the image)
# stays image-supplied, so an immutable example or a stack upgrade is always the fresh image's.
NB_HOME = os.environ.get("NOTEBOOK_HOME", "/home/jovyan")
NB_WORK_DIR = NB_HOME + "/work"
# Per-user volume / PVC name — {username} is templated by the spawner. The username is already
# tenant-bound (ADR-0014), so the volume is per user within one tenant.
NB_VOLUME_TEMPLATE = os.environ.get("NOTEBOOK_VOLUME_TEMPLATE", "industryflow-nb-{username}")
# Idle reclamation (ADR-0011 dec 1/7, made concrete in ADR-0020): cull servers idle past the
# timeout. Reclaiming is free — the work is safe on the volume and respawns from a fresh image.
IDLE_TIMEOUT_SECONDS = int(os.environ.get("NOTEBOOK_IDLE_TIMEOUT_SECONDS", "3600"))
CULL_EVERY_SECONDS = int(os.environ.get("NOTEBOOK_CULL_EVERY_SECONDS", "300"))

c = get_config()  # noqa: F821  (provided by JupyterHub at load time)

# Mount the hub under a path prefix so it can be embedded same-origin in the platform UI
# (ADR-0014 single-origin): the frontend reverse-proxies <app>/jupyter/ → SSO proxy → hub, so the
# session cookie is first-party and the iframe is same-origin. Defaults to "/" for a standalone
# deployment. JupyterHub propagates base_url to chp and every single-user server.
c.JupyterHub.base_url = os.environ.get("NOTEBOOK_BASE_URL", "/")

# Migrate the hub's own state database on start, instead of refusing to boot against an older one.
# JupyterHub 5 carries a schema its 4.x predecessor did not, and a hub that finds the old schema
# does not warn — it exits, and the container crash-loops:
#
#   Found database schema version 0eee8c825d24 != 4621fec11365.
#   Backup your database and run `jupyterhub upgrade-db`
#
# That is a real defect for an in-place upgrade: an operator who pulls a new image gets a dead hub
# and a manual migration step nobody wrote down. This is what the trait is for (it is what
# `jupyterhub upgrade-db` runs), and it is a no-op once the schema matches.
#
# Safe here because of what this database *is*: hub session state — users, servers, API tokens —
# not the user's work. ADR-0020 dec 1 puts authored notebooks on a per-user durable volume, and
# ADR-0012 mints every credential per session, so nothing in here is a system of record. JupyterHub
# also backs a SQLite database up itself immediately before migrating it.
c.JupyterHub.upgrade_db = True

# Allow the platform UI to embed the notebook same-origin (ADR-0014). The hub and each single-user
# server default to `Content-Security-Policy: frame-ancestors 'none'`, which blocks framing even
# from the same origin — so the /notebooks iframe would be refused. Relax it to `'self'`: the embed
# is same-origin (same host), and no cross-origin site may frame it. Set on the hub and propagated
# to every spawned server.
_FRAME_CSP = "frame-ancestors 'self'"
c.JupyterHub.tornado_settings = {"headers": {"Content-Security-Policy": _FRAME_CSP}}
c.Spawner.args = [
    "--ServerApp.tornado_settings="
    + "{'headers': {'Content-Security-Policy': \"" + _FRAME_CSP + "\"}}"
]

# ---------------------------------------------------------------------------
# Authentication — trust the verified identity the proxy forwards (ADR-0014)
# ---------------------------------------------------------------------------
from jupyterhub.auth import Authenticator  # noqa: E402
from jupyterhub.handlers import BaseHandler  # noqa: E402
from tornado import web  # noqa: E402


class _ProxyHeaderLoginHandler(BaseHandler):
    """Establish a hub session from the proxy-forwarded verified identity.

    The hub is reachable only through the trusted SSO proxy, which validates the platform session
    and overwrites any client-supplied identity headers (ADR-0014 dec 2-3); this handler does not
    re-verify the session token.
    """

    async def get(self):
        try:
            who = ident.parse_identity(self.request.headers)
        except ValueError:
            raise web.HTTPError(401, "missing or invalid verified identity")
        user = await self.auth_to_user(
            {"name": who.user, "auth_state": {"company_id": who.company_id, "role": who.role}}
        )
        self.set_login_cookie(user)
        self.redirect(self.get_next_url(user))


class ProxyHeaderAuthenticator(Authenticator):
    """Header-trusting authenticator; the trusted proxy is the actual verifier (ADR-0014).

    A hub login is established once from the forwarded identity, but the hub cookie outlives the
    request that set it — so every ``auth_refresh_age`` (and before every spawn) the login is
    re-checked against the identity the proxy forwards *now* (``refresh_user``). A different user
    behind the cookie, or the same user with another tenant or role, ends the login; for the same
    user the running servers are stopped too, because their capabilities were minted against
    facts that no longer hold.
    """

    enable_auth_state = True

    def get_handlers(self, app):
        return [(r"/login", _ProxyHeaderLoginHandler)]

    async def authenticate(self, handler, data):  # not used; login is header-driven
        return None

    async def refresh_user(self, user, handler=None):
        if handler is None:
            return True  # no request to judge (an internal refresh): nothing has changed
        verdict = ident.reconcile(
            handler.request.headers, user.name, await user.get_auth_state() or {}
        )
        if verdict == ident.RECONCILE_STALE_FACTS:
            await _stop_active_servers(handler, user)
        return verdict in (ident.RECONCILE_SAME, ident.RECONCILE_NOT_FORWARDED)


async def _stop_active_servers(handler, user):
    for name, spawner in list(user.spawners.items()):
        if spawner.active and not spawner.pending:
            await handler.stop_single_user(user, name)


c.JupyterHub.authenticator_class = ProxyHeaderAuthenticator
c.Authenticator.auth_refresh_age = AUTH_REFRESH_SECONDS
c.Authenticator.refresh_pre_spawn = True
# Logging out of the hub stops the user's servers, and stopping a server revokes its capabilities
# (post_stop_hook below). The platform's sign-out logs out of the hub before it ends the platform
# session, and the platform's logout endpoint revokes the user's capabilities centrally as well.
c.JupyterHub.shutdown_on_logout = True

# ---------------------------------------------------------------------------
# Spawn profiles — role-driven caps (ADR-0011 dec 5/7), spawner-independent
# ---------------------------------------------------------------------------
PROFILE_RESOURCES = {
    ident.PROFILE_ANALYTICS: {"cpu_limit": 0.5, "mem_limit": "1G"},
    ident.PROFILE_AUTHORING: {"cpu_limit": 2.0, "mem_limit": "4G"},
}


_STORE = None


def _capability_store():
    """The Redis-backed capability store (ADR-0015 dec 7), built once per hub process."""
    global _STORE
    if _STORE is None:
        import redis  # runtime dep; imported lazily so config import doesn't require it

        _STORE = cap.RedisCapabilityStore(redis.Redis.from_url(os.environ["REDIS_URL"]))
    return _STORE


async def revoke_server_capabilities(spawner):
    """post_stop_hook: a stopped server's handles stop working now, not when they expire
    (ADR-0012 dec 6, ADR-0015 dec 2)."""
    revoked = await asyncio.to_thread(
        cap.revoke_server, _capability_store(), spawner.user.name, spawner.name
    )
    spawner.log.info("revoked %d capability handle(s) of %s", revoked, spawner._log_name)


async def keep_capabilities_alive(spawner) -> bool:
    """Refresh a running server's handles. Returns False when none is left to refresh.

    None left means they were revoked from outside the hub — the platform's logout revokes all of a
    user's handles — so the environment holds credentials that no longer work, and the caller stops
    it. A store that cannot be reached is not that: the server is left alone, and its handles keep
    their remaining lifetime (the verifiers fail closed on their own).
    """
    try:
        live = await asyncio.to_thread(
            cap.refresh_server, _capability_store(), spawner.user.name, spawner.name,
            CAPABILITY_TTL_SECONDS,
        )
    except Exception:  # noqa: BLE001 - a store outage must not stop every server
        spawner.log.exception("could not refresh the capabilities of %s", spawner._log_name)
        return True
    return live > 0


def with_capability_lifecycle(base):
    """The deployment's spawner class with the capability lifecycle attached (ADR-0015 dec 2).

    Every poll of a running server refreshes its handles; a server whose handles were all revoked
    reports itself stopped at its next poll, so the hub takes the same path as for a server that
    died — route removed, server stopped, post_stop_hook run — and the user's next visit spawns a
    fresh one with fresh handles.
    """

    class CapabilityLifecycleSpawner(base):
        _capabilities_revoked = False

        async def start(self):
            self._capabilities_revoked = False
            return await super().start()

        async def poll(self):
            if self._capabilities_revoked:
                return 0
            return await super().poll()

        async def poll_and_notify(self):
            status = await super().poll_and_notify()
            if status is None and not await keep_capabilities_alive(self):
                self.log.warning("capabilities of %s were revoked; stopping it", self._log_name)
                self._capabilities_revoked = True
            return status

    CapabilityLifecycleSpawner.__name__ = CapabilityLifecycleSpawner.__qualname__ = (
        f"CapabilityLifecycle{base.__name__}"
    )
    return CapabilityLifecycleSpawner


async def bind_identity_and_profile(spawner):
    """Bind the environment to its tenant, enforce the role profile, and mint capabilities.

    Spawner-agnostic: the identity env, resource caps, and capability handles are set the same way
    for KubeSpawner and DockerSpawner; only how the attribution labels are attached differs.
    """
    auth_state = await spawner.user.get_auth_state() or {}
    who = ident.Identity(
        user=spawner.user.name,
        company_id=auth_state.get("company_id", ""),
        role=auth_state.get("role", ""),
    )
    # Identity only — this carries no data credential (ADR-0012 dec 5).
    spawner.environment.update(ident.pod_environment(who))

    labels = ident.pod_labels(who)
    if SPAWNER == "docker":
        # DockerSpawner attaches labels via the container create kwargs.
        existing = dict(getattr(spawner, "extra_create_kwargs", {}) or {})
        existing_labels = dict(existing.get("labels", {}))
        existing_labels.update(labels)
        existing["labels"] = existing_labels
        spawner.extra_create_kwargs = existing
    else:
        spawner.extra_labels = {**getattr(spawner, "extra_labels", {}), **labels}

    # The profile is chosen by role, not offered to the user (ADR-0011 dec 5).
    profile = ident.select_profile(who.role)
    resources = PROFILE_RESOURCES[profile]
    spawner.cpu_limit = resources["cpu_limit"]
    spawner.mem_limit = resources["mem_limit"]

    # Per-profile image + landing surface (ADR-0011 dec 5): authoring gets JupyterLab + the DS
    # stack; analytics gets the Voila read-only renderer (no free-form code editor). Works the
    # same for DockerSpawner and KubeSpawner — both honour spawner.image / spawner.default_url.
    spawner.image = ident.image_for_profile(profile, os.environ)
    if profile == ident.PROFILE_ANALYTICS:
        spawner.default_url = "/voila"

    # Mint per-session, single-tenant capabilities and inject the handles (ADR-0015): the kernel's
    # only data credentials — opaque handles, not DB passwords, revocable by deleting their store
    # entries. Every profile gets the data-API capability; authoring also gets SQL. Each is recorded
    # against this server, so stopping it revokes them and polling it keeps them alive.
    store = _capability_store()
    # A respawn replaces the environment, so it replaces the credentials: whatever an earlier run of
    # this server still holds (a stop the hub never saw, e.g. across a hub crash) is revoked first.
    await asyncio.to_thread(cap.revoke_server, store, who.user, spawner.name)

    def mint(audience):
        return cap.mint(
            store, user=who.user, company_id=who.company_id, audience=audience,
            ttl_seconds=CAPABILITY_TTL_SECONDS, server_name=spawner.name,
        )

    spawner.environment["INDUSTRYFLOW_API_CAPABILITY"] = mint(cap.AUDIENCE_API)
    if profile == ident.PROFILE_AUTHORING:
        spawner.environment["INDUSTRYFLOW_SQL_CAPABILITY"] = mint(cap.AUDIENCE_SQL)
        # Where the kernel reaches the SQL access proxy (ADR-0015); the capability above is the
        # password it presents there. Only authoring kernels get SQL, so only they get the URL.
        if os.environ.get("INDUSTRYFLOW_SQL_PROXY_URL"):
            spawner.environment["INDUSTRYFLOW_SQL_PROXY_URL"] = os.environ["INDUSTRYFLOW_SQL_PROXY_URL"]
        # Experiment tracking (ADR-0019): a tracking-audience capability the kernel presents to the
        # tracking gateway as its MLflow bearer token. Authoring-only — operators author no models.
        if os.environ.get("INDUSTRYFLOW_TRACKING_URL"):
            spawner.environment["INDUSTRYFLOW_TRACKING_CAPABILITY"] = mint(cap.AUDIENCE_TRACKING)
            # MLflow reads these: the gateway endpoint + the capability as MLFLOW_TRACKING_TOKEN.
            spawner.environment["MLFLOW_TRACKING_URI"] = os.environ["INDUSTRYFLOW_TRACKING_URL"]
            spawner.environment["MLFLOW_TRACKING_TOKEN"] = spawner.environment["INDUSTRYFLOW_TRACKING_CAPABILITY"]
        # Cold layer (ADR-0025 dec 5, read side): a cold-audience capability the kernel presents to
        # the cold-store broker to read long-horizon training history as Parquet. Authoring-only —
        # operators (analytics) train no models. The broker resolves the handle to this one tenant
        # and vends read-only, prefix-scoped pre-signed GET URLs; the kernel holds no object-store key.
        if os.environ.get("INDUSTRYFLOW_COLD_BROKER_URL"):
            spawner.environment["INDUSTRYFLOW_COLD_CAPABILITY"] = mint(cap.AUDIENCE_COLD)
            spawner.environment["INDUSTRYFLOW_COLD_BROKER_URL"] = os.environ["INDUSTRYFLOW_COLD_BROKER_URL"]
    # The blessed data path the client uses (ADR-0011 dec 4): the gateway origin, reachable from
    # the single-user environment. Identity-only; the capability above is what authorises it.
    if os.environ.get("INDUSTRYFLOW_API_URL"):
        spawner.environment["INDUSTRYFLOW_API_URL"] = os.environ["INDUSTRYFLOW_API_URL"]

    # Durable per-user work storage (ADR-0020) — authoring only; operators (analytics) author
    # nothing. work/ persists across the ephemeral container; the capabilities minted above are
    # NEVER written here — they are re-minted every spawn, so a restored volume carries notebooks,
    # not credentials. DockerSpawner uses a per-user named volume; KubeSpawner an auto-ensured PVC.
    if profile == ident.PROFILE_AUTHORING:
        if SPAWNER == "docker":
            spawner.volumes = {NB_VOLUME_TEMPLATE: NB_WORK_DIR}
        else:
            spawner.storage_pvc_ensure = True
            spawner.pvc_name_template = NB_VOLUME_TEMPLATE
            spawner.storage_capacity = os.environ.get("NOTEBOOK_STORAGE_CAPACITY", "2Gi")
            spawner.volumes = [{"name": "work", "persistentVolumeClaim": {"claimName": NB_VOLUME_TEMPLATE}}]
            spawner.volume_mounts = [{"name": "work", "mountPath": NB_WORK_DIR}]


# ---------------------------------------------------------------------------
# Durable work + idle reclamation (ADR-0020), spawner-agnostic
# ---------------------------------------------------------------------------
# File-browser root: the home, so a data scientist sees both examples/ (image, read-only) and
# work/ (their persistent volume).
c.Spawner.notebook_dir = NB_HOME

# A stopped server's capabilities are revoked at once (ADR-0015 dec 2), for either spawner.
c.Spawner.post_stop_hook = revoke_server_capabilities

# Implement ADR-0011 dec 1/7 "reclaimed when idle": cull idle servers with a managed service that
# holds a MINIMAL scoped token (ADR-0020 dec 2) — least-privilege, not an ambient admin credential.
c.JupyterHub.services = [
    {
        "name": "idle-culler",
        "command": [
            sys.executable, "-m", "jupyterhub_idle_culler",
            "--timeout=%d" % IDLE_TIMEOUT_SECONDS,
            "--cull-every=%d" % CULL_EVERY_SECONDS,
        ],
    },
]
c.JupyterHub.load_roles = [
    {
        "name": "idle-culler",
        "services": ["idle-culler"],
        # Exactly what the culler needs to find and stop idle servers — nothing more.
        "scopes": ["list:users", "read:users:activity", "read:servers", "delete:servers"],
    },
]


# ---------------------------------------------------------------------------
# Spawner wiring — KubeSpawner (pods) or DockerSpawner (containers), per ADR-0018
# ---------------------------------------------------------------------------
if SPAWNER == "docker":
    from dockerspawner import DockerSpawner  # noqa: E402

    c.JupyterHub.spawner_class = with_capability_lifecycle(DockerSpawner)
    c.DockerSpawner.pre_spawn_hook = bind_identity_and_profile
    # The per-user image is chosen per profile in the hook (above); set a class default so the
    # image is runnable standalone, and the network it shares with the hub + proxy.
    c.DockerSpawner.image = os.environ.get("NOTEBOOK_IMAGE_AUTHORING") or os.environ.get("NOTEBOOK_IMAGE", "")
    c.DockerSpawner.network_name = os.environ["NOTEBOOK_NETWORK"]
    c.DockerSpawner.use_internal_ip = True
    # Ephemeral by default (ADR-0011 dec 1): drop the container when the server stops.
    c.DockerSpawner.remove = True
    c.DockerSpawner.debug = False
    # Containment (ADR-0011 dec 7 / ADR-0018 dec 3-4): non-root, no new privileges, drop all caps.
    # The image already declares a non-root USER; pin it explicitly as defence in depth.
    c.DockerSpawner.extra_create_kwargs = {"user": "1000:1000"}
    c.DockerSpawner.extra_host_config = {
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
    }
    # How the single-user container reaches the hub API, and the hub's public (chp) bind. On
    # Compose the hub is addressed by its service name; chp is hub-managed (internal).
    c.JupyterHub.hub_ip = "0.0.0.0"
    c.JupyterHub.hub_connect_ip = os.environ.get("HUB_CONNECT_IP", "notebook-hub")
    c.JupyterHub.bind_url = os.environ.get("HUB_PUBLIC_URL", "http://0.0.0.0:8000")
else:
    from kubespawner import KubeSpawner  # noqa: E402

    c.JupyterHub.spawner_class = with_capability_lifecycle(KubeSpawner)
    c.KubeSpawner.pre_spawn_hook = bind_identity_and_profile
    # Containment (ADR-0009 / ADR-0011 dec 7). Network egress is constrained by NetworkPolicy.
    c.KubeSpawner.uid = 1000
    c.KubeSpawner.fs_gid = 1000
    c.KubeSpawner.privileged = False
    c.KubeSpawner.allow_privilege_escalation = False
    c.KubeSpawner.extra_container_config = {
        "securityContext": {
            "runAsNonRoot": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        }
    }
    # fs_gid=1000 makes a freshly-provisioned PVC group-writable by the non-root uid. Per-user
    # persistence for the authoring profile is ensured in the spawn hook (ADR-0020); analytics
    # spawns request no storage, so the class default stays off here.
    c.KubeSpawner.storage_pvc_ensure = False
