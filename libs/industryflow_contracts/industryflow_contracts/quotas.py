# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tenant quotas (ADR-0049): the caps, their configuration and the names of their shared counts.

Each cap has a platform default in the deployment's configuration (``TENANT_QUOTA_<CAP>``): a positive
whole number, ``off``, or, for a cap with a derived capacity (``DERIVED``), a share ``<N>%`` of what the
overrides leave of the deployment's capacity (ADR-0049 dec 2, 14; ADR-0050 dec 4); unset takes the shipped
default, which is finite (ADR-0049 dec 6). A platform
superuser may override a cap for one tenant; the override is the tenant's data in the database, and
reaches ingestion on the tenant-status stream, and the hub and the SQL proxy as an expiring copy in the
capability store (``cap_key``), which the device-facing ingestion service cannot reach. Every service
reads the same names and the same parse from here.

Standard library only: this is imported by every service image.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional, Union

from . import tenancy

# The caps (ADR-0049 dec 1, 9): device readings per second, notebook environments running at once, and a
# tenant's SQL load through the SQL proxy: sessions open at once, seconds one statement may run, seconds a
# transaction may stay idle.
READINGS_PER_SECOND = "readings_per_second"
NOTEBOOK_ENVIRONMENTS = "notebook_environments"
SQL_SESSIONS = "sql_sessions"
SQL_STATEMENT_SECONDS = "sql_statement_seconds"
SQL_IDLE_TRANSACTION_SECONDS = "sql_idle_transaction_seconds"
# The data a tenant keeps in each store whose capacity tenants share, in GiB (ADR-0049 dec 11).
STORED_DATABASE_GIB = "stored_database_gib"
STORED_OBJECT_GIB = "stored_object_gib"
CAPS = (READINGS_PER_SECOND, NOTEBOOK_ENVIRONMENTS, SQL_SESSIONS, SQL_STATEMENT_SECONDS,
        SQL_IDLE_TRANSACTION_SECONDS, STORED_DATABASE_GIB, STORED_OBJECT_GIB)

# Each shared store, as the measurement names it, and its cap.
STORED_CAPS = {"database": STORED_DATABASE_GIB, "object": STORED_OBJECT_GIB}
GIB = 1 << 30

# The shipped amounts (ADR-0049 dec 6), sized against the reference node of docs/operations/capacity.md
# (one 8 GB, 4-core node: 500 readings/s), so one tenant cannot take the whole node. The readings and the
# notebook environments take these only where the deployment derives no capacity for them (SHIPPED_SHARE
# where it does). A tenant's SQL sessions are a tenth of the notebook share of
# the database's connections (SQL_SESSIONS_TOTAL_SHIPPED).
# A tenant's stored data is a fifth of the reference node's 232 GB disk in the database and two fifths in
# the object store, which share it.
SHIPPED_DEFAULTS = {READINGS_PER_SECOND: 250, NOTEBOOK_ENVIRONMENTS: 3, SQL_SESSIONS: 5,
                    SQL_STATEMENT_SECONDS: 300, SQL_IDLE_TRANSACTION_SECONDS: 60,
                    STORED_DATABASE_GIB: 40, STORED_OBJECT_GIB: 80}

# The configuration naming each cap's platform default.
ENV = {cap: f"TENANT_QUOTA_{cap.upper()}" for cap in CAPS}

# The caps whose capacity the deployment derives from its resources (ADR-0050 dec 4, scripts/host-memory.py),
# and the configuration naming it: the readings the ingest path carries per second, and the notebook sessions
# the pool holds at once. Unset where nothing derives it (a cluster): those caps then take amounts.
DERIVED = {READINGS_PER_SECOND: "INGEST_CAPACITY_READINGS_PER_SECOND",
           NOTEBOOK_ENVIRONMENTS: "NOTEBOOK_SESSION_CAPACITY"}


@dataclass(frozen=True)
class Share:
    """A default that is a share of what the overrides leave of a derived capacity (ADR-0049 dec 2, 14)."""

    percent: int

    def __str__(self) -> str:
        return f"{self.percent}%"


# The shipped default of a cap with a derived capacity, where the deployment derives it (ADR-0049 dec 6): half
# of what the overrides leave, so one tenant cannot take the whole capacity on any host.
SHIPPED_SHARE = Share(50)

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


def parse_setting(cap: str, raw: Optional[str], capacity: Optional[int] = None) -> Union[int, Share, None]:
    """A platform default as configured: ``None`` for ``off``, else the number, or a ``Share`` of a cap whose
    ``capacity`` the deployment derives. Unset or empty takes the shipped default: ``SHIPPED_SHARE`` where the
    capacity is derived, else the amount. Anything else, a share without a derived capacity included, is a
    ``ValueError``, so a deployment that mistypes it does not start."""
    check_cap(cap)
    derived = cap in DERIVED and capacity is not None
    if raw is None or raw.strip() == "":
        return SHIPPED_SHARE if derived else SHIPPED_DEFAULTS[cap]
    text = raw.strip()
    if text.lower() == OFF:
        return None
    if text.endswith("%"):
        if not derived:
            raise ValueError(f"{ENV[cap]} is a share ({raw!r}), but this deployment derives no capacity for "
                             f"{cap}: set an amount")
        number = text[:-1].strip()
        if not number.isdigit() or not 1 <= int(number) <= 100:
            raise ValueError(f"{ENV[cap]} must be a share from 1% to 100%, got {raw!r}")
        return Share(int(number))
    try:
        return check_value(int(text))
    except ValueError:
        shares = " or a share '<N>%'" if derived else ""
        raise ValueError(f"{ENV[cap]} must be '{OFF}', a whole number from 1 to {MAX_VALUE}{shares}, "
                         f"got {raw!r}") from None


def capacities(environ: Mapping[str, str] = os.environ) -> dict:
    """The derived capacity of each cap of ``DERIVED`` the deployment sets; ``ValueError`` on one it cannot
    read."""
    out = {}
    for cap, name in DERIVED.items():
        raw = environ.get(name)
        if raw is None or raw.strip() == "":
            continue
        if not raw.strip().isdigit():
            raise ValueError(f"{name} must be a whole number, got {raw!r}: run scripts/host-memory.py")
        out[cap] = int(raw.strip())
    return out


def settings(environ: Mapping[str, str] = os.environ) -> dict:
    """Every cap's platform default as configured: an amount, ``None`` (off) or a ``Share``."""
    held = capacities(environ)
    return {cap: parse_setting(cap, environ.get(ENV[cap]), held.get(cap)) for cap in CAPS}


def resolve(setting: Union[int, Share, None], capacity: Optional[int], reserved: int = 0) -> Optional[int]:
    """The cap a tenant without an override takes: an amount or off as configured; a share of what ``reserved``
    (every override of the cap) leaves of ``capacity``, at least 1, so a tenant is never refused everything."""
    if not isinstance(setting, Share):
        return setting
    return max(1, max(0, int(capacity or 0) - reserved) * setting.percent // 100)


def defaults(environ: Mapping[str, str] = os.environ) -> dict:
    """Every cap's platform default as an enforcer holds it when no effective cap reaches it: a share of the
    whole capacity (ADR-0049 dec 14); ``ValueError`` on a value it cannot read."""
    held = capacities(environ)
    return {cap: resolve(setting, held.get(cap)) for cap, setting in settings(environ).items()}


def effective_caps(setting: Union[int, Share, None], capacity: Optional[int], overrides: Mapping[str, int],
                   tenants) -> dict:
    """``{company_id: cap}`` for every tenant of ``tenants``: its override, else the default resolved against
    what every override of the cap (``overrides``, ``{company_id: value}``) leaves of ``capacity``."""
    default = resolve(setting, capacity, sum(overrides.values()))
    return {company_id: overrides.get(company_id, default) for company_id in tenants}


def overcommitted(capacity: Optional[int], overrides: Mapping[str, int]) -> bool:
    """Whether the overrides of a cap exceed its derived capacity, as when the resources shrink (dec 14)."""
    return capacity is not None and sum(overrides.values()) > capacity


# The SQL sessions of every tenant together (ADR-0049 dec 10): the share of the database's connections its
# budget gives them (infrastructure/timescaledb/config/postgresql.conf), divided among the SQL proxy's
# replicas. A platform setting, not a tenant's cap: no override, and never off, since an unbounded sum is
# what the budget exists to prevent.
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


# A tenant's SQL sessions open now (ADR-0049 dec 4, 9): a sorted set of session leases scored by their
# expiry, so a session whose proxy stopped without ending it leaves the count when its lease runs out.
# Every replica of the SQL proxy counts in the same set. Every tenant's together are not counted here:
# each replica holds its own part of the total (dec 10), so the total never depends on this store.
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


# A measurement older than this is not trusted (ADR-0049 dec 8): the tenant's stored-data caps are
# waived until a newer one, and the operator is alerted. Three runs of the hourly measurement job.
USAGE_STALE_ENV = "TENANT_USAGE_STALE_SECONDS"
USAGE_STALE_SHIPPED = 3 * 3600


def usage_stale_seconds(environ: Mapping[str, str] = os.environ) -> int:
    """How old a measurement may be and still hold; ``ValueError`` on a value it cannot read."""
    raw = environ.get(USAGE_STALE_ENV)
    if raw is None or raw.strip() == "":
        return USAGE_STALE_SHIPPED
    try:
        return check_value(int(raw.strip()))
    except ValueError:
        raise ValueError(f"{USAGE_STALE_ENV} must be a whole number of seconds from 1 to {MAX_VALUE}, "
                         f"got {raw!r}") from None


def stored_over(usage: Mapping[str, tuple[int, float]], caps: Mapping[str, Optional[int]], now: float,
                stale_after: float) -> frozenset:
    """The stored-data caps a tenant is over: ``usage`` maps a store to ``(bytes, measured_at)`` (unix
    seconds), ``caps`` a cap to its effective value (``None`` off). A measurement missing or older than
    ``stale_after`` holds nothing (dec 8)."""
    over = set()
    for store, cap in STORED_CAPS.items():
        limit = caps.get(cap)
        measured = usage.get(store)
        if limit is None or measured is None or now - measured[1] > stale_after:
            continue
        if measured[0] > limit * GIB:
            over.add(cap)
    return frozenset(over)


# A tenant over a stored-data cap, as the publisher copies it into the capability store for the tracking
# gateway, which refuses its artifact writes (dec 12). It expires as a tenant-status message goes stale.
def over_key(cap: str, company_id) -> str:
    return f"quota:over:{check_cap(cap)}:{tenancy.canonical_company_id(company_id)}"


# A tenant's override, as the publisher copies it from the registry into the capability store for the
# hub and the SQL proxy (not the quota store, which ingestion can write). It expires as a tenant-status message goes
# stale, so a stopped publisher returns every tenant to the defaults.
def cap_key(cap: str, company_id) -> str:
    return f"quota:cap:{check_cap(cap)}:{tenancy.canonical_company_id(company_id)}"
