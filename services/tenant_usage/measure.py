# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Each tenant's stored data, measured in each shared store (ADR-0049 dec 11).

The database measures itself: a SECURITY DEFINER function sums every tenant schema's relations, the
chunks of its hypertables included. The object store is listed under each of the tenant's prefixes, in
the cold layer's bucket and the artifact bucket, and a key counts only when the one test of ownership
says it is the tenant's (ADR-0039 dec 1). Nothing is tallied by the writers, so a deletion, a failed write
or a writer that does not count is seen.

The job writes nothing to either store and deletes nothing (dec 13): its object-store principal may only
list, and its database role only measure and record. A tenant whose listing fails keeps its last
measurement, which ages until the API gateway alerts on it; the run measures the others and then fails.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable, Iterable, Protocol

from industryflow_contracts import tenancy

logger = logging.getLogger("tenant_usage")


class Database(Protocol):
    def tenant_database_bytes(self) -> Iterable[tuple[str, int]]: ...
    def record(self, company_id: str, store: str, size: int) -> None: ...


class ObjectStore(Protocol):
    def list_objects(self, bucket: str, prefix: str) -> Iterable[tuple[str, int]]: ...


@dataclass(frozen=True)
class Bucket:
    """A bucket of the object store and how a tenant's prefix in it is named."""

    name: str
    prefix: Callable[[str], str]


def buckets(cold_bucket: str | None, artifact_bucket: str | None) -> tuple[Bucket, ...]:
    """The tenant's prefixes: its cold history and its model and run artifacts (with its admitted uploads),
    in whichever of the two buckets the deployment runs."""
    where = []
    if cold_bucket:
        where.append(Bucket(cold_bucket, tenancy.cold_key_prefix))
    if artifact_bucket:
        where.append(Bucket(artifact_bucket, tenancy.mlflow_artifact_prefix))
    return tuple(where)


def object_bytes(store: ObjectStore, where: Iterable[Bucket], company_id: str) -> int:
    """The bytes of every object under the tenant's prefixes that the ownership test gives it."""
    total = 0
    for bucket in where:
        prefix = bucket.prefix(company_id)
        for key, size in store.list_objects(bucket.name, prefix):
            if tenancy.owns_key(prefix, key):
                total += int(size)
    return total


def run(db: Database, store: ObjectStore, where: Iterable[Bucket]) -> int:
    """Measure and record every tenant in both stores; the number of tenants whose measurement failed."""
    where = tuple(where)
    failed = 0
    for company_id, size in db.tenant_database_bytes():
        db.record(company_id, "database", int(size))
        try:
            db.record(company_id, "object", object_bytes(store, where, company_id))
        except Exception:  # noqa: BLE001 - this tenant's last measurement stays and ages; the others go on
            logger.exception("object-store measurement failed for tenant %s", company_id)
            failed += 1
    return failed
