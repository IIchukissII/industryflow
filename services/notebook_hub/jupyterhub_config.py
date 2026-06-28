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
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import capabilities as cap  # noqa: E402
import identity as ident  # noqa: E402

# Which spawner profile this deployment runs (ADR-0018). Default kube preserves the original
# Kubernetes behaviour; the Compose deployment sets NOTEBOOK_SPAWNER=docker.
SPAWNER = os.environ.get("NOTEBOOK_SPAWNER", "kube").strip().lower()

# Capability lifetime (ADR-0015 dec 2). The store entry is the backstop; the hub keeps it alive
# while the session is healthy and deletes it on logout (deferred lifecycle). Config-owned.
CAPABILITY_TTL_SECONDS = int(os.environ.get("NOTEBOOK_CAPABILITY_TTL_SECONDS", "3600"))

c = get_config()  # noqa: F821  (provided by JupyterHub at load time)

# Mount the hub under a path prefix so it can be embedded same-origin in the platform UI
# (ADR-0014 single-origin): the frontend reverse-proxies <app>/jupyter/ → SSO proxy → hub, so the
# session cookie is first-party and the iframe is same-origin. Defaults to "/" for a standalone
# deployment. JupyterHub propagates base_url to chp and every single-user server.
c.JupyterHub.base_url = os.environ.get("NOTEBOOK_BASE_URL", "/")

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
    """Header-trusting authenticator; the trusted proxy is the actual verifier (ADR-0014)."""

    enable_auth_state = True

    def get_handlers(self, app):
        return [(r"/login", _ProxyHeaderLoginHandler)]

    async def authenticate(self, handler, data):  # not used; login is header-driven
        return None


c.JupyterHub.authenticator_class = ProxyHeaderAuthenticator

# ---------------------------------------------------------------------------
# Spawn profiles — role-driven caps (ADR-0011 dec 5/7), spawner-independent
# ---------------------------------------------------------------------------
PROFILE_RESOURCES = {
    ident.PROFILE_ANALYTICS: {"cpu_limit": 0.5, "mem_limit": "1G"},
    ident.PROFILE_AUTHORING: {"cpu_limit": 2.0, "mem_limit": "4G"},
}


def _capability_store():
    """Build the Redis-backed capability store (ADR-0015 dec 7)."""
    import redis  # runtime dep; imported lazily so config import doesn't require it

    return cap.RedisCapabilityStore(redis.Redis.from_url(os.environ["REDIS_URL"]))


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

    # Mint per-session, single-tenant, read-only capabilities and inject the handles (ADR-0015):
    # the kernel's only data credentials — opaque handles, not DB passwords, revocable by deleting
    # their store entries. Every profile gets the data-API capability; authoring also gets SQL.
    store = _capability_store()
    spawner.environment["INDUSTRYFLOW_API_CAPABILITY"] = cap.mint(
        store, user=who.user, company_id=who.company_id,
        audience=cap.AUDIENCE_API, ttl_seconds=CAPABILITY_TTL_SECONDS,
    )
    if profile == ident.PROFILE_AUTHORING:
        spawner.environment["INDUSTRYFLOW_SQL_CAPABILITY"] = cap.mint(
            store, user=who.user, company_id=who.company_id,
            audience=cap.AUDIENCE_SQL, ttl_seconds=CAPABILITY_TTL_SECONDS,
        )
        # Where the kernel reaches the SQL access proxy (ADR-0015); the capability above is the
        # password it presents there. Only authoring kernels get SQL, so only they get the URL.
        if os.environ.get("INDUSTRYFLOW_SQL_PROXY_URL"):
            spawner.environment["INDUSTRYFLOW_SQL_PROXY_URL"] = os.environ["INDUSTRYFLOW_SQL_PROXY_URL"]
    # The blessed data path the client uses (ADR-0011 dec 4): the gateway origin, reachable from
    # the single-user environment. Identity-only; the capability above is what authorises it.
    if os.environ.get("INDUSTRYFLOW_API_URL"):
        spawner.environment["INDUSTRYFLOW_API_URL"] = os.environ["INDUSTRYFLOW_API_URL"]


# ---------------------------------------------------------------------------
# Spawner wiring — KubeSpawner (pods) or DockerSpawner (containers), per ADR-0018
# ---------------------------------------------------------------------------
if SPAWNER == "docker":
    c.JupyterHub.spawner_class = "dockerspawner.DockerSpawner"
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
    c.JupyterHub.spawner_class = "kubespawner.KubeSpawner"
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
    # Ephemeral by default; per-user persistence for authoring is a deferred decision.
    c.KubeSpawner.storage_pvc_ensure = False
