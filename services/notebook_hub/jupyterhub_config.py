# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
JupyterHub configuration for the IndustryFlow notebook hub (ADR-0011, ADR-0012, ADR-0014).

REFERENCE CONFIGURATION — not yet cluster-validated. The decision logic (identity parsing,
role→profile, label/env binding) lives in identity.py and IS unit-tested; this file is the
JupyterHub/KubeSpawner wiring around it and must be validated against a running hub before use.

What it encodes:
  * SSO from the platform session via the trusted proxy (ADR-0014): the hub trusts the verified
    identity headers the proxy forwards and runs no login of its own.
  * Role-driven, enforced spawn profiles (ADR-0011 dec 5): the profile is derived from the
    verified role, NOT chosen by the user.
  * Identity-only environment (ADR-0012 dec 5): the kernel gets its tenant/identity, never a
    data credential — those are minted separately by the spawner step (deferred).
  * Contained pods (ADR-0009 / ADR-0011 dec 7): non-root, no privilege escalation, dropped
    capabilities, resource limits. Network egress is constrained by NetworkPolicy (Helm).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import identity as ident  # noqa: E402

c = get_config()  # noqa: F821  (provided by JupyterHub at load time)

# ---------------------------------------------------------------------------
# Authentication — trust the verified identity the proxy forwards (ADR-0014)
# ---------------------------------------------------------------------------
from jupyterhub.auth import Authenticator  # noqa: E402
from jupyterhub.handlers import BaseHandler  # noqa: E402
from tornado import web  # noqa: E402


class _ProxyHeaderLoginHandler(BaseHandler):
    """Establish a hub session from the proxy-forwarded verified identity.

    The hub is reachable only through the trusted proxy, which validates the platform session
    and overwrites any client-supplied identity headers (ADR-0014 dec 2-3); this handler does
    not re-verify the session token.
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
# Spawner — role-driven profile, identity-only env, contained pods
# ---------------------------------------------------------------------------
# Per-profile resource caps. Values are deployment-owned and intentionally modest defaults
# (ADR-0011 dec 7); tune in the deployment, not here.
PROFILE_RESOURCES = {
    ident.PROFILE_ANALYTICS: {"cpu_limit": 0.5, "mem_limit": "1G"},
    ident.PROFILE_AUTHORING: {"cpu_limit": 2.0, "mem_limit": "4G"},
}


async def bind_identity_and_profile(spawner):
    """Bind the environment to its tenant and enforce the role-derived profile (ADR-0011 dec 5)."""
    auth_state = await spawner.user.get_auth_state() or {}
    who = ident.Identity(
        user=spawner.user.name,
        company_id=auth_state.get("company_id", ""),
        role=auth_state.get("role", ""),
    )
    # Identity only — never a data credential (ADR-0012 dec 5).
    spawner.environment.update(ident.pod_environment(who))
    spawner.extra_labels = {**getattr(spawner, "extra_labels", {}), **ident.pod_labels(who)}

    # The profile is chosen by role, not offered to the user.
    resources = PROFILE_RESOURCES[ident.select_profile(who.role)]
    spawner.cpu_limit = resources["cpu_limit"]
    spawner.mem_limit = resources["mem_limit"]


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
# Ephemeral by default; per-user persistence for the authoring profile is a deferred decision.
c.KubeSpawner.storage_pvc_ensure = False
