# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Carries out requested tenant deletions, store by store (ADR-0033 dec 4).

The database says which stores a deletion must empty (``company_deletion_required_stores()``) and
records each one emptied; this walks the pending deletions and, for every store not yet recorded,
runs that store's step and records what it removed. A store this deployment has no step for, and one
whose step has done its part but is not yet empty (the tracking store awaits its server's collection),
is left unrecorded, so the deletion stays pending and the run says what it is waiting on. Once every
store is recorded the database itself is emptied, last, and the tenant becomes a tombstone.

Every step is idempotent and the record is written after the step succeeds, so a run that dies part
way repeats at most the step it was in. One run at a time: a run that finds the lock held exits.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Protocol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Removal:
    """What one step removed, and until when the store may still hold the tenant (if it ages it out)."""

    detail: dict
    retained_until: Optional[datetime] = None


# A step empties one store of one tenant: (company_id, user_ids, requested_at) -> what it removed, or
# None while the store is not yet empty; the next run calls it again.
Step = Callable[[str, list, datetime], Optional[Removal]]


class Registry(Protocol):
    def run_lock(self): ...
    def required_stores(self) -> list: ...
    def pending(self) -> list: ...                     # [(company_id, requested_at, removed stores)]
    def user_ids(self, company_id: str) -> list: ...
    def record(self, company_id: str, store: str, removal: Removal) -> None: ...
    def complete(self, company_id: str, backup_retention: timedelta) -> dict: ...


@dataclass
class Outcome:
    completed: list = field(default_factory=list)
    waiting: dict = field(default_factory=dict)         # company_id -> stores not yet empty
    failed: dict = field(default_factory=dict)          # company_id -> error


def run(registry: Registry, steps: dict, backup_retention: timedelta) -> Optional[Outcome]:
    """One pass over every pending deletion. None when another run holds the lock."""
    with registry.run_lock() as held:
        if not held:
            logger.warning("another tenant-delete run holds the lock; skipping")
            return None
        required = registry.required_stores()
        outcome = Outcome()
        for company_id, requested_at, removed in registry.pending():
            try:
                _delete(registry, steps, required, company_id, requested_at, set(removed), backup_retention,
                        outcome)
            except Exception as e:  # noqa: BLE001 - one tenant's failure must not stop the others
                logger.exception("tenant %s: deletion failed; the next run resumes it", company_id)
                outcome.failed[company_id] = repr(e)
        return outcome


def _delete(registry, steps, required, company_id, requested_at, removed, backup_retention, outcome):
    users = None
    waiting = []
    for store in required:
        if store in removed:
            continue
        step = steps.get(store)
        if step is None:
            waiting.append(store)
            continue
        if users is None:
            users = registry.user_ids(company_id)
        removal = step(company_id, users, requested_at)
        if removal is None:
            waiting.append(store)
            continue
        registry.record(company_id, store, removal)
        logger.info("tenant %s: removed from %s %s", company_id, store, removal.detail)
    if waiting:
        outcome.waiting[company_id] = waiting
        logger.warning("tenant %s: waiting on %s; the database is emptied last", company_id, waiting)
        return
    detail = registry.complete(company_id, backup_retention)
    outcome.completed.append(company_id)
    logger.info("tenant %s: deleted %s", company_id, detail)


def broker_step(retention: timedelta, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> Step:
    """The shared broker ages a tenant's in-flight readings out within its retention (ADR-0033 dec 4).
    Ingestion has refused the tenant since its suspension, so nothing newer than now remains."""
    def step(company_id, users, requested_at=None):
        return Removal(detail={"retention_hours": retention.total_seconds() / 3600},
                       retained_until=clock() + retention)
    return step
