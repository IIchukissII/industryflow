# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quotas (ADR-0049): the caps, their configuration and the names of their shared counts.

Each cap has a platform default in the deployment's configuration (``TENANT_QUOTA_<CAP>``): a positive
whole number, or ``off``; unset takes the shipped default, which is finite (ADR-0049 dec 6). A platform
superuser may override a cap for one tenant; the override is the tenant's data in the database, and
reaches ingestion on the tenant-status stream, and the hub and the SQL proxy as an expiring copy in the
capability store (``cap_key``), which the device-facing ingestion service cannot reach. Every service
reads the same names and the same parse from here.

Standard library only: this is imported by every service image.
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

from . import tenancy

# The caps (ADR-0049 dec 1, 9): device readings per second, notebook environments running at once, and a
# tenant's SQL load through the SQL proxy: sessions open at once, seconds one statement may run, seconds a
# transaction may stay idle.
READINGS_PER_SECOND = "readings_per_second"
NOTEBOOK_ENVIRONMENTS = "notebook_environments"
SQL_SESSIONS = "sql_sessions"
SQL_STATEMENT_SECONDS = "sql_statement_seconds"
SQL_IDLE_TRANSACTION_SECONDS = "sql_idle_transaction_seconds"
CAPS = (READINGS_PER_SECOND, NOTEBOOK_ENVIRONMENTS, SQL_SESSIONS, SQL_STATEMENT_SECONDS,
        SQL_IDLE_TRANSACTION_SECONDS)

# The shipped defaults (ADR-0049 dec 6), sized against the reference node of docs/operations/capacity.md
# (one 8 GB, 4-core node: 500 readings/s with headroom; a notebook environment takes up to 4 GB), so
# one tenant cannot take the whole node. A tenant's SQL sessions are a tenth of the notebook share of
# the database's connections (SQL_SESSIONS_TOTAL_SHIPPED).
SHIPPED_DEFAULTS = {READINGS_PER_SECOND: 250, NOTEBOOK_ENVIRONMENTS: 3, SQL_SESSIONS: 5,
                    SQL_STATEMENT_SECONDS: 300, SQL_IDLE_TRANSACTION_SECONDS: 60}

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


# The SQL sessions of every tenant together (ADR-0049 dec 10): the share of the database's connections its
# budget gives them (infrastructure/timescaledb/config/postgresql.conf). A platform setting, not a tenant's
# cap: no override, and never off, since an unbounded sum is what the budget exists to prevent.
SQL_SESSIONS_TOTAL_ENV = "NOTEBOOK_SQL_SESSIONS_TOTAL"
SQL_SESSIONS_TOTAL_SHIPPED = 50


def sql_sessions_total(environ: Mapping[str, str] = os.environ) -> int:
    """The configured total of every tenant's SQL sessions; ``ValueError`` on a value it cannot read."""
    raw = environ.get(SQL_SESSIONS_TOTAL_ENV)
    if raw is None or raw.strip() == "":
        return SQL_SESSIONS_TOTAL_SHIPPED
    try:
        return check_value(int(raw.strip()))
    except ValueError:
        raise ValueError(f"{SQL_SESSIONS_TOTAL_ENV} must be a whole number from 1 to {MAX_VALUE}, "
                         f"got {raw!r}") from None


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


# A tenant's SQL sessions open now, and every tenant's together (ADR-0049 dec 4, 9, 10): sorted sets of
# session leases scored by their expiry, so a session whose proxy stopped without ending it leaves the
# count when its lease runs out. Every replica of the SQL proxy counts in the same two sets.
SQL_SESSIONS_ALL_KEY = "quota:sql-sessions:all"


def sql_sessions_key(company_id) -> str:
    return f"quota:sql-sessions:{tenancy.canonical_company_id(company_id)}"


# Refusals at the total of every tenant's SQL sessions: a platform ceiling, not a tenant's cap, so a count
# with no tenant only, which the API gateway reports on the platform plane (ADR-0049 dec 5, 10).
SQL_SESSIONS_TOTAL_REFUSALS_KEY = "quota:refusals-total:sql_sessions_total"


# Sessions the SQL proxy admitted without their count because the quota store could not be read
# (ADR-0049 dec 8): counted in the capability store, since the quota store is the one that failed, and
# reported by the API gateway on the platform plane. Per cap, no tenant.
def count_unavailable_key(cap: str) -> str:
    return f"quota:count-unavailable:{check_cap(cap)}"


# A tenant's override, as the publisher copies it from the registry into the capability store for the
# hub and the SQL proxy (not the quota store, which ingestion can write). It expires as a tenant-status message goes
# stale, so a stopped publisher returns every tenant to the defaults.
def cap_key(cap: str, company_id) -> str:
    return f"quota:cap:{check_cap(cap)}:{tenancy.canonical_company_id(company_id)}"
