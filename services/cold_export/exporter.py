# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cold-layer export orchestration (ADR-0025 decisions 3, 4).

The whole safety of the cold layer lives in one ordering, enforced here and nowhere else:

    export  ->  verify  ->  drop

An orchestrator restarts a failed task; it does not resurrect dropped data. So the drop of a
source chunk happens only after the day's Parquet has been written AND its row count reconciled
against the source (decision 3). No chunk is dropped without a verified export.

The run is idempotent and self-healing (decision 4): "export everything older than N days not
yet exported." A day whose manifest already exists is not re-exported; a day that was exported
and verified but whose source chunk was not yet dropped (a crash between verify and drop) is
resumed straight to the drop on the next run. A failure raises, failing the run, and the next
run picks the day up again — the orchestrator is almost incidental.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from .naming import company_id_to_prefix
from .ports import ColdStore, ExportError, MeasurementSource, Tenant

logger = logging.getLogger(__name__)


def run_export(source: MeasurementSource, store: ColdStore, horizon_days: int, today: date) -> dict:
    """
    Export every tenant's raw measurements older than ``horizon_days`` and drop the exported
    chunks. ``today`` is injected (not read from the clock here) so the horizon is deterministic
    and the run is unit-testable. Returns a per-outcome tally for logging/metrics.

    One tenant failing does not abort the others: its error is logged and the run continues, but
    the overall run still reports failures so the orchestrator surfaces a non-zero result. The
    per-day ordering guarantee is unaffected — a failing tenant simply drops nothing.
    """
    cutoff = today - timedelta(days=horizon_days)
    tally: dict[str, int] = {}
    failures = 0
    tenants = source.list_tenants()
    logger.info("Cold export starting: %d tenant(s), horizon=%dd, cutoff=%s",
                len(tenants), horizon_days, cutoff.isoformat())

    for tenant in tenants:
        try:
            tenant_tally = export_tenant(source, store, tenant, cutoff)
            for outcome, n in tenant_tally.items():
                tally[outcome] = tally.get(outcome, 0) + n
        except Exception:  # noqa: BLE001 — isolate one tenant's failure from the rest of the run
            failures += 1
            logger.exception("Cold export FAILED for tenant %s (%s); no chunk dropped",
                             tenant.company_id, tenant.schema_name)

    logger.info("Cold export finished: outcomes=%s, tenant_failures=%d", tally, failures)
    if failures:
        raise ExportError(f"cold export completed with {failures} tenant failure(s): {tally}")
    return tally


def export_tenant(source: MeasurementSource, store: ColdStore, tenant: Tenant, cutoff: date) -> dict:
    """Export all days older than ``cutoff`` for one tenant, oldest first."""
    prefix = company_id_to_prefix(tenant.company_id)  # validates the UUID (ADR-0025 dec 5)
    tally: dict[str, int] = {}
    for day in source.exportable_days(tenant.schema_name, older_than=cutoff):
        outcome = export_day(source, store, tenant.schema_name, prefix, day)
        tally[outcome] = tally.get(outcome, 0) + 1
    return tally


def export_day(source: MeasurementSource, store: ColdStore, schema: str, prefix: str, day: date) -> str:
    """
    Export one day and drop its chunk, idempotently. Returns the outcome for tallying.

    Ordering is the invariant: `_write_and_verify` returns only on a reconciled write, and only
    then is `drop_day` called. Any mismatch raises ExportError *before* the drop.
    """
    src_count = source.count_day(schema, day)
    manifest = store.read_manifest(prefix, day)

    if manifest is not None:
        # A verified export already exists. Guard its integrity before trusting it.
        pq_count = store.parquet_row_count(prefix, day)
        if pq_count != manifest["rows"]:
            raise ExportError(
                f"cold Parquet corrupt for {schema} {day}: footer {pq_count} != manifest {manifest['rows']}"
            )
        if src_count == 0:
            # Source chunk already dropped on a previous run — the day is fully done.
            return "already_done"
        if src_count != manifest["rows"]:
            # Late-arriving rows landed in this day after the earlier export. Re-export to
            # capture them (idempotent overwrite), then drop.
            _write_and_verify(source, store, schema, prefix, day, src_count)
            source.drop_day(schema, day)
            return "reexported"
        # Verified export matches the source: a prior run crashed between verify and drop.
        # Resume straight to the drop (decision 4, self-healing).
        source.drop_day(schema, day)
        return "resumed_drop"

    if src_count == 0:
        # Nothing to export and nothing exported (e.g. an empty day boundary). Skip.
        return "empty"

    _write_and_verify(source, store, schema, prefix, day, src_count)
    source.drop_day(schema, day)
    return "exported"


def _write_and_verify(source: MeasurementSource, store: ColdStore, schema: str, prefix: str,
                      day: date, src_count: int) -> None:
    """
    Write the day's Parquet and reconcile it against the source, then write the manifest.

    Two independent checks must both pass before the manifest (the "verified" marker) is written:
      1. the writer emitted exactly ``src_count`` rows, and
      2. the Parquet footer read back from the store reports exactly ``src_count`` rows.
    Either mismatch raises ExportError, so the caller never reaches `drop_day` (decision 3). The
    manifest is written last: its presence is the proof-of-verification the next run relies on.
    """
    result = store.write_parquet(prefix, day, source.read_day(schema, day))
    if result.rows != src_count:
        raise ExportError(
            f"export row mismatch for {schema} {day}: wrote {result.rows} != source {src_count}"
        )
    pq_count = store.parquet_row_count(prefix, day)
    if pq_count != src_count:
        raise ExportError(
            f"verify failed for {schema} {day}: Parquet footer {pq_count} != source {src_count}"
        )
    store.write_manifest(prefix, day, {
        "schema": schema,
        "day": day.isoformat(),
        "rows": src_count,
    })
    logger.info("Verified cold export: %s %s (%d rows)", schema, day.isoformat(), src_count)
