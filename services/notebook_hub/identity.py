# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Notebook hub identity and spawn-profile logic (ADR-0014, ADR-0011 dec 5, ADR-0012 dec 5).

Pure, dependency-free decision logic so it can be unit-tested without a hub or a cluster:

  * read the verified identity the trusted proxy forwards (ADR-0014 dec 2-4),
  * choose the spawn profile from the role (ADR-0011 dec 5),
  * derive the pod labels and environment that bind the environment to its tenant.

It deliberately holds NO data credential: the environment carries identity only; what the
kernel may reach is minted separately by the spawner (ADR-0012 dec 5). jupyterhub_config.py
wires JupyterHub/KubeSpawner around this module.
"""
from __future__ import annotations

import hmac
import re
import uuid
from dataclasses import dataclass
from typing import Mapping

# Headers the trusted proxy forwards after validating the platform session (ADR-0014 dec 2).
# The proxy MUST overwrite any client-supplied values (ADR-0002 dec 3).
HEADER_USER = "X-IF-User"
HEADER_COMPANY_ID = "X-IF-Company-Id"
HEADER_ROLE = "X-IF-Role"

# The SSO proxy's credential (ADR-0014 dec 3). Reachability is not proof of origin: every spawned
# kernel must reach the hub API, and the login route is served by the same application, so a
# kernel can send the identity headers too. The forwarded identity is honoured only on a request
# that also carries the secret shared between the hub and the proxy, which the proxy sets on hub
# traffic (overwriting any client value) and which no kernel holds.
HEADER_PROXY_SECRET = "X-IF-SSO-Proxy-Secret"
PROXY_SECRET_ENV = "NOTEBOOK_SSO_PROXY_SECRET"
# Alphanumeric only: the proxy embeds the value in its nginx configuration, where a quote, a `$` or
# whitespace would change its meaning. `openssl rand -hex 32` gives 64 such characters.
_PROXY_SECRET_SHAPE = re.compile(r"[A-Za-z0-9]{32,}")
_PLACEHOLDER_PREFIXES = ("change_me", "changeme")

# Spawn profiles (ADR-0011 dec 5).
PROFILE_ANALYTICS = "analytics"  # operators: read-only, rendered dashboards
PROFILE_AUTHORING = "authoring"  # data scientists: full authoring

# Roles that receive the authoring profile; everyone else gets read-only analytics. The
# concrete mapping is deployment-owned config (ADR-0014 deferred); this is the default.
DEFAULT_AUTHORING_ROLES = frozenset({"admin", "engineer", "data_scientist"})


@dataclass(frozen=True)
class Identity:
    """A verified user identity forwarded by the trusted proxy."""

    user: str
    company_id: str
    role: str


def _header(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive header lookup (proxies/servers vary on header casing)."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return ""


def parse_identity(headers: Mapping[str, str]) -> Identity:
    """Build an Identity from the proxy-forwarded headers, validating the tenant.

    Raises ValueError if the user, tenant, or role is missing, or if the tenant is not a
    well-formed UUID — the same injection-defence discipline the rest of the platform applies
    before a company_id is used to scope anything (ADR-0003).
    """
    user = _header(headers, HEADER_USER).strip()
    company_id = _header(headers, HEADER_COMPANY_ID).strip()
    role = _header(headers, HEADER_ROLE).strip()

    if not user:
        raise ValueError("missing verified user")
    if not role:
        raise ValueError("missing verified role")
    # Canonicalise + validate the tenant; a non-UUID raises before it is used anywhere.
    company_id = str(uuid.UUID(company_id))

    return Identity(user=user, company_id=company_id, role=role)


def validate_proxy_secret(value: str) -> str:
    """The hub's copy of the SSO proxy secret, or ValueError if it is unset or unfit.

    The hub refuses to start without one (fail closed): a hub that cannot tell the proxy from a
    kernel must not accept a forwarded identity at all.
    """
    value = (value or "").strip()
    if not value:
        raise ValueError(f"{PROXY_SECRET_ENV} is not set")
    if value.lower().startswith(_PLACEHOLDER_PREFIXES):
        raise ValueError(f"{PROXY_SECRET_ENV} is still a placeholder")
    if not _PROXY_SECRET_SHAPE.fullmatch(value):
        raise ValueError(f"{PROXY_SECRET_ENV} must be at least 32 alphanumeric characters")
    return value


def from_proxy(headers: Mapping[str, str], secret: str) -> bool:
    """True only when the request carries the SSO proxy's secret (constant-time comparison).

    An empty expected secret matches nothing, so a misconfigured hub refuses every forwarded
    identity rather than accepting every one.
    """
    if not secret:
        return False
    presented = _header(headers, HEADER_PROXY_SECRET)
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), secret.encode("utf-8"))


# Outcomes of re-checking an established hub login against the identity forwarded now.
RECONCILE_SAME = "same"                    # the login still describes who is behind it
RECONCILE_NOT_FORWARDED = "not-forwarded"  # no forwarded identity: not browser traffic via the proxy
RECONCILE_INVALID = "invalid"              # a forwarded identity that does not parse
RECONCILE_OTHER_USER = "other-user"        # someone else is behind the hub cookie now
RECONCILE_STALE_FACTS = "stale-facts"      # same user, but another tenant or role


def reconcile(headers: Mapping[str, str], hub_user: str, auth_state: Mapping[str, str]) -> str:
    """Compare an established hub login with the identity the proxy forwards on this request.

    Browser traffic always reaches the hub through the SSO proxy, which always forwards the
    verified identity (ADR-0014 dec 2-3). A request carrying none of it is not browser traffic —
    it is a token-authenticated call from inside the deployment (a single-user server reporting
    activity) — and there is nothing to compare, which is not the same as a mismatch. The caller
    passes only headers that came from the proxy (``from_proxy``); any other request's identity
    headers are not evidence and must not reach this function.
    """
    if not any(_header(headers, h).strip() for h in (HEADER_USER, HEADER_COMPANY_ID, HEADER_ROLE)):
        return RECONCILE_NOT_FORWARDED
    try:
        who = parse_identity(headers)
    except ValueError:
        return RECONCILE_INVALID
    if who.user != hub_user:
        return RECONCILE_OTHER_USER
    if who.company_id != auth_state.get("company_id") or who.role != auth_state.get("role"):
        return RECONCILE_STALE_FACTS
    return RECONCILE_SAME


def select_profile(role: str, authoring_roles: frozenset[str] = DEFAULT_AUTHORING_ROLES) -> str:
    """Map a role to a spawn profile (ADR-0011 dec 5). Unknown roles get least privilege."""
    return PROFILE_AUTHORING if role in authoring_roles else PROFILE_ANALYTICS


# Per-profile single-user images (ADR-0011 dec 5): the authoring profile gets the JupyterLab + DS
# image; the analytics profile gets the Voila read-only renderer. Env names the deployment sets.
ANALYTICS_IMAGE_ENV = "NOTEBOOK_IMAGE_ANALYTICS"
AUTHORING_IMAGE_ENV = "NOTEBOOK_IMAGE_AUTHORING"
_PROFILE_IMAGE_ENV = {PROFILE_ANALYTICS: ANALYTICS_IMAGE_ENV, PROFILE_AUTHORING: AUTHORING_IMAGE_ENV}


def image_for_profile(profile: str, env: Mapping[str, str]) -> str:
    """The single-user image for a profile: its per-profile env var, else the legacy single
    ``NOTEBOOK_IMAGE`` (back-compat / a one-image dev setup). Raises if neither is set."""
    image = env.get(_PROFILE_IMAGE_ENV[profile]) or env.get("NOTEBOOK_IMAGE")
    if not image:
        raise KeyError(
            f"no image configured for the {profile} profile "
            f"(set {_PROFILE_IMAGE_ENV[profile]} or NOTEBOOK_IMAGE)"
        )
    return image


def pod_labels(identity: Identity) -> dict[str, str]:
    """Labels for the single-user pod — used by NetworkPolicy targeting and attribution.

    A UUID (36 chars, ``[0-9a-f-]``) is a valid Kubernetes label value.
    """
    return {
        "app.kubernetes.io/name": "notebook-singleuser",
        "app.kubernetes.io/component": "notebook",
        "industryflow.io/company-id": identity.company_id,
        "industryflow.io/profile": select_profile(identity.role),
    }


def pod_environment(identity: Identity) -> dict[str, str]:
    """Identity-only environment for the kernel. NOT a data credential (ADR-0012 dec 5)."""
    return {
        "INDUSTRYFLOW_USER": identity.user,
        "INDUSTRYFLOW_COMPANY_ID": identity.company_id,
        "INDUSTRYFLOW_PROFILE": select_profile(identity.role),
    }
