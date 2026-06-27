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

import uuid
from dataclasses import dataclass
from typing import Mapping

# Headers the trusted proxy forwards after validating the platform session (ADR-0014 dec 2).
# The proxy MUST overwrite any client-supplied values (ADR-0002 dec 3); the hub trusts these
# only because it is reachable solely through that proxy.
HEADER_USER = "X-IF-User"
HEADER_COMPANY_ID = "X-IF-Company-Id"
HEADER_ROLE = "X-IF-Role"

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


def select_profile(role: str, authoring_roles: frozenset[str] = DEFAULT_AUTHORING_ROLES) -> str:
    """Map a role to a spawn profile (ADR-0011 dec 5). Unknown roles get least privilege."""
    return PROFILE_AUTHORING if role in authoring_roles else PROFILE_ANALYTICS


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
