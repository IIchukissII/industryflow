# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Failure classes for the alert worker's consumer (ADR-0005 dec 3).

A reading that cannot be handled fails for one of two reasons, and they need opposite treatment:

- the reading itself is bad (malformed, missing a field, an id that is not a UUID). Retrying cannot
  fix it, so it goes straight to the dead-letter topic;
- the platform under it is unavailable (the database, its pool, the network). The reading is fine,
  so it must never be dead-lettered: the batch is rewound and retried until the dependency is back.
"""
import asyncio

import asyncpg

# SQLSTATE classes that describe the server or the connection, not the statement:
# 08 connection exception, 40 transaction rollback (serialization failure, deadlock),
# 53 insufficient resources (too many connections, out of memory), 57 operator intervention
# (admin shutdown, cannot connect now), 58 system error.
_TRANSIENT_SQLSTATE_CLASSES = {"08", "40", "53", "57", "58"}


class MessageValidationError(ValueError):
    """The reading itself is unprocessable. Dead-lettered without retry."""


class RulesUnavailable(RuntimeError):
    """The reading's tenant has no rules the worker could read: what applies to it is unknown, not
    empty, so the reading is never committed unevaluated (ADR-0005 dec 3). It fails as the read
    failed: a dependency failure is retried until the dependency is back, any other one is retried
    a bounded number of times and dead-lettered, so one tenant's broken rules cannot hold the
    others' readings."""

    def __init__(self, company_id: str, cause: BaseException):
        super().__init__(f"rules for company {company_id} could not be read: {cause}")
        self.cause = cause


def is_infrastructure_error(exc: BaseException) -> bool:
    """True when exc is a dependency failure that a retry can outlive (never dead-lettered)."""
    if isinstance(exc, MessageValidationError):
        return False
    if isinstance(exc, RulesUnavailable):
        return is_infrastructure_error(exc.cause)
    if isinstance(exc, (OSError, asyncio.TimeoutError, asyncpg.InterfaceError)):
        # OSError covers ConnectionError and socket failures; InterfaceError covers a closed pool
        # or a connection lost mid-call.
        return True
    if isinstance(exc, asyncpg.PostgresError):
        sqlstate = getattr(exc, "sqlstate", None) or ""
        return sqlstate[:2] in _TRANSIENT_SQLSTATE_CLASSES
    return False
