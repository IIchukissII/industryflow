# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quotas (ADR-0049): the caps, their configuration and the names of their shared counts.

Each cap has a platform default in the deployment's configuration (``TENANT_QUOTA_<CAP>``): a positive
whole number, or ``off``; unset takes the shipped default, which is finite (ADR-0049 dec 6). A platform
superuser may override a cap for one tenant; the override is the tenant's data in the database, and
reaches ingestion on the tenant-status stream and the hub as an expiring copy in the capability store
(``cap_key``), which the device-facing ingestion service cannot reach. Every service reads the same names and the same parse from here.

Standard library only: this is imported by every service image.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

from . import tenancy

# The caps (ADR-0049 dec 1): device readings per second, and notebook environments running at once.
READINGS_PER_SECOND = "readings_per_second"
NOTEBOOK_ENVIRONMENTS = "notebook_environments"
CAPS = (READINGS_PER_SECOND, NOTEBOOK_ENVIRONMENTS)

# The shipped defaults (ADR-0049 dec 6), sized against the reference node of docs/operations/capacity.md
# (one 8 GB, 4-core node: 500 readings/s with headroom; a notebook environment takes up to 4 GB), so
# one tenant cannot take the whole node.
SHIPPED_DEFAULTS = {READINGS_PER_SECOND: 250, NOTEBOOK_ENVIRONMENTS: 3}

# The configuration naming each cap's platform default.
ENV = {cap: f"TENANT_QUOTA_{cap.upper()}" for cap in CAPS}

# The word that turns a cap off; only the configuration may say it (ADR-0049 dec 6).
OFF = "off"

# The largest value a default or an override may take: a bound on typing errors, not a sizing.
MAX_VALUE = 1_000_000


def check_cap(cap: str) -> str:
    """The cap, if it is one; ``ValueError`` otherwise."""
    if cap not in CAPS:
        raise ValueError(f"no tenant quota {cap!r}; the caps are {', '.join(CAPS)}")
    return cap


def check_value(value) -> int:
    """An override's value: a whole number from 1 to ``MAX_VALUE``; ``ValueError`` otherwise."""
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_VALUE:
        raise ValueError(f"a tenant quota is a whole number from 1 to {MAX_VALUE}")
    return value


def parse_setting(cap: str, raw: Optional[str]) -> Optional[int]:
    """A platform default as configured: ``None`` for ``off``, the shipped default when unset or empty,
    else the number. Anything else is a ``ValueError``, so a deployment that mistypes it does not start."""
    check_cap(cap)
    if raw is None or raw.strip() == "":
        return SHIPPED_DEFAULTS[cap]
    text = raw.strip()
    if text.lower() == OFF:
        return None
    try:
        return check_value(int(text))
    except ValueError:
        raise ValueError(f"{ENV[cap]} must be '{OFF}' or a whole number from 1 to {MAX_VALUE}, "
                         f"got {raw!r}") from None


def defaults(environ: Mapping[str, str] = os.environ) -> dict:
    """Every cap's platform default from the configuration; ``ValueError`` on a value it cannot read."""
    return {cap: parse_setting(cap, environ.get(ENV[cap])) for cap in CAPS}


def effective(default: Optional[int], override: Optional[int]) -> Optional[int]:
    """The cap that holds for a tenant: its override, else the platform default (``None`` is off)."""
    return override if override is not None else default


# ── The quota store (ADR-0049 dec 4, 5; ADR-0002 dec 3): a Redis apart from the capability store ────
# Counters only, every key expiring, no credential: a lost store only restarts them (ADR-0049 dec 7).

# A tenant's readings in one second of the clock: a fixed one-second window.
READINGS_WINDOW_SECONDS = 1
READINGS_KEY_TTL_SECONDS = 2


def readings_key(company_id, second: int) -> str:
    return f"quota:readings:{tenancy.canonical_company_id(company_id)}:{int(second)}"


# Which tenants a cap refused, per day: read by a superuser, never on the platform plane (ADR-0049 dec 5).
# Every key naming a tenant is a day's and expires after REFUSALS_KEEP_DAYS, so a deleted tenant leaves
# the store within that time.
REFUSALS_KEEP_DAYS = 8


def refusals_key(cap: str, day: str) -> str:
    """The hash ``company_id -> refusals`` of ``cap`` on ``day`` (``YYYYMMDD``, UTC)."""
    return f"quota:refusals:{check_cap(cap)}:{day}"


def refused_last_key(cap: str, day: str) -> str:
    """The hash ``company_id -> unix time of its last refusal`` of ``cap`` on ``day``."""
    return f"quota:refused-last:{check_cap(cap)}:{day}"


def refusals_total_key(cap: str) -> str:
    """The count of every refusal of ``cap``, with no tenant: what every enforcing service adds to, and the
    API gateway reports on the platform plane (ADR-0049 dec 5)."""
    return f"quota:refusals-total:{check_cap(cap)}"


# A tenant's override, as the publisher copies it from the registry into the capability store for the
# hub (not the quota store, which ingestion can write). It expires as a tenant-status message goes
# stale, so a stopped publisher returns every tenant to the defaults.
def cap_key(cap: str, company_id) -> str:
    return f"quota:cap:{check_cap(cap)}:{tenancy.canonical_company_id(company_id)}"
