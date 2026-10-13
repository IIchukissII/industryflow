# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Cold-layer export orchestration (ADR-0025 decisions 3, 4).

The whole safety of the cold layer lives in one ordering, enforced here and nowhere else:

    snapshot the day  ->  export  ->  verify  ->  list in the manifest  ->  checked drop

An orchestrator restarts a failed task; it does not resurrect dropped data. So the source rows of
a day are dropped only after a Parquet part holding them has been written, its row count
reconciled against the source, and the part listed in the day's manifest (decision 3). The export
reads one database snapshot of the day and the database fingerprints it; the drop locks the day,
re-reads the fingerprint, and drops only if the day still holds exactly the exported rows. A row
that arrived meanwhile therefore defers the drop instead of being dropped unexported; the next run
exports it.

A verified, committed part is never rewritten. Rows that land in a day after its drop (late
arrivals) are exported as an additional part with its own reconciled count, and the manifest lists
every part — readers read all of them. The only part ever overwritten is a *pending* one: listed,
but whose drop never committed (a crash between the two). Its rows are then all still in the
source, so it is re-exported under the same name. Whether a drop committed is not guessed from row
counts: the database records it in the same transaction as the drop (`drop_recorded`).

The run is idempotent and self-healing (decision 4): "export everything older than N days not
yet exported." A failure raises, failing the run, and the next run picks the day up again — the
orchestrator is almost incidental.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterator, Optional

import pyarrow as pa

from .naming import company_id_to_prefix, company_id_to_schema, part_file
from .ports import ColdStore, ExportError, MeasurementSource, Tenant

logger = logging.getLogger(__name__)

MANIFEST_FORMAT = 2

# The natural key of a measurement (the hot table's unique constraint). The archive holds each key
# once: a row whose key a committed part already holds is not exported again.
KEY_COLUMNS = ("time", "sensor_id")


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
    """Export all days older than ``cutoff`` for one tenant, oldest first.

    The schema is derived from the company_id by the canonical mapping (ADR-0003 dec 5), never
    taken from the registry row. A registry ``schema_name`` that disagrees with the derivation
    means the registry no longer describes this tenant; the tenant is refused, not guessed at.
    """
    prefix = company_id_to_prefix(tenant.company_id)  # validates the UUID (ADR-0025 dec 5)
    schema = company_id_to_schema(tenant.company_id)
    if tenant.schema_name != schema:
        raise ExportError(
            f"registry schema_name {tenant.schema_name!r} for company {tenant.company_id} is not "
            f"its canonical schema {schema!r}; refusing to export this tenant"
        )
    tally: dict[str, int] = {}
    for day in source.exportable_days(schema, older_than=cutoff):
        outcome = export_day(source, store, schema, prefix, day)
        tally[outcome] = tally.get(outcome, 0) + 1
    return tally


def export_day(source: MeasurementSource, store: ColdStore, schema: str, prefix: str, day: date) -> str:
    """
    Export one day's source rows as a verified Parquet part and drop them, idempotently. Returns
    the outcome for tallying.

    Ordering is the invariant: the part is written, reconciled and listed in the manifest before
    `drop` is called. Any mismatch raises ExportError *before* the drop, and the drop itself does
    nothing unless the day still holds exactly the rows the snapshot exported.
    """
    manifest = store.read_manifest(prefix, day)
    parts = manifest_parts(manifest)
    legacy = manifest is not None and "parts" not in manifest
    # A part is appended only after the previous part's drop committed, so only the last listed part
    # can be pending. A legacy (single-file) manifest predates the drop record; its file is treated
    # as committed, and the de-duplication below covers the case where its drop never ran.
    pending = bool(parts) and not legacy and not source.drop_recorded(schema, day, parts[-1]["file"])
    committed = parts[:-1] if pending else parts
    for part in committed:
        _check_part(store, schema, prefix, day, part)

    with source.snapshot_day(schema, day) as snapshot:
        if snapshot.rows == 0:
            if not pending:
                # Nothing in the source: either never anything (an empty day boundary) or a day
                # that was exported and dropped on an earlier run.
                return "already_done" if parts else "empty"
            # The pending part's rows left the source without our drop (a deliberate delete). The
            # part is still a verified copy of rows that existed: verify it, then record its drop.
            _check_part(store, schema, prefix, day, parts[-1])
            return "resumed_drop" if snapshot.drop(parts[-1]["file"]) else "drop_deferred"

        # A pending part's rows are still in the source unless deliberately deleted (its drop did not
        # commit), so it is re-exported under its own name rather than appended to. Committed parts
        # are never rewritten.
        base = committed
        part = part_file(len(base))
        batches: Iterator[pa.RecordBatch] = snapshot.batches()
        expected = snapshot.rows
        if base:
            # Every row is exported once: a source row whose natural key a committed part already
            # holds (a redelivered duplicate of an archived reading, or a legacy day whose drop never
            # ran) is not written again.
            fresh = _without_archived(batches, snapshot.rows, store, prefix, day, base)
            expected = fresh.num_rows
            batches = iter(fresh.to_batches())

        if expected:
            _write_and_verify(store, schema, prefix, day, part, batches, expected)
            parts_after = base + [{"file": part, "rows": expected}]
        else:
            # Every source row is already archived: the drop completes the last committed part.
            parts_after = base
            part = base[-1]["file"]

        # Listed before the drop: a crash after this line leaves a pending part the next run
        # re-exports (its drop did not commit) or confirms (it did) — never an unlisted export.
        store.write_manifest(prefix, day, _manifest(schema, day, parts_after))
        if not snapshot.drop(part):
            # Rows arrived in, changed in, or left the day after the snapshot. Nothing was dropped;
            # the part stays pending and the next run re-exports it with the day as it is then.
            logger.warning("Cold export drop deferred: %s %s changed during the export (%s pending)",
                           schema, day.isoformat(), part)
            return "drop_deferred"

    if not expected:
        return "resumed_drop"
    if pending:
        return "reexported"
    return "late_rows_exported" if base else "exported"


def manifest_parts(manifest: Optional[dict]) -> list[dict]:
    """The verified parts a manifest lists, oldest first, as ``[{"file", "rows"}]``.

    A manifest written before a day could hold several parts names no parts: it stands for the
    single first file holding ``rows`` rows.
    """
    if manifest is None:
        return []
    if "parts" in manifest:
        return [{"file": str(p["file"]), "rows": int(p["rows"])} for p in manifest["parts"]]
    return [{"file": part_file(0), "rows": int(manifest["rows"])}]


def _manifest(schema: str, day: date, parts: list[dict]) -> dict:
    return {
        "format": MANIFEST_FORMAT,
        "schema": schema,
        "day": day.isoformat(),
        "rows": sum(p["rows"] for p in parts),
        "parts": parts,
    }


def _check_part(store: ColdStore, schema: str, prefix: str, day: date, part: dict) -> None:
    """A committed part's footer must still match its manifest count, or nothing is trusted."""
    footer = store.part_row_count(prefix, day, part["file"])
    if footer != part["rows"]:
        raise ExportError(
            f"cold Parquet corrupt for {schema} {day} {part['file']}: "
            f"footer {footer} != manifest {part['rows']}"
        )


def _without_archived(batches: Iterator[pa.RecordBatch], source_rows: int, store: ColdStore,
                      prefix: str, day: date, base: list[dict]) -> pa.Table:
    """The streamed source rows whose natural key no committed part holds, in file sort order."""
    table = pa.Table.from_batches(list(batches)) if source_rows else None
    if table is None or table.num_rows != source_rows:
        streamed = 0 if table is None else table.num_rows
        raise ExportError(
            f"source row mismatch for {prefix} {day}: streamed {streamed} != counted {source_rows}"
        )
    archived = pa.concat_tables([
        store.read_part_keys(prefix, day, p["file"]).select(list(KEY_COLUMNS)) for p in base
    ])
    fresh = table.join(archived, keys=list(KEY_COLUMNS), join_type="left anti")
    # A join does not keep row order, and the within-file sort is what Parquet min/max statistics
    # prune on (ADR-0025 dec 6).
    fresh = fresh.select(table.column_names).cast(table.schema)
    return fresh.sort_by([("equipment_id", "ascending"), ("time", "ascending")])


def _write_and_verify(store: ColdStore, schema: str, prefix: str, day: date, part: str,
                      batches: Iterator[pa.RecordBatch], expected: int) -> None:
    """
    Write one Parquet part and reconcile it against the source.

    Two independent checks must both pass before the part may be listed in the manifest:
      1. the writer emitted exactly ``expected`` rows, and
      2. the Parquet footer read back from the store reports exactly ``expected`` rows.
    Either mismatch raises ExportError, so the caller never reaches the manifest or the drop.
    """
    result = store.write_part(prefix, day, part, batches)
    if result.rows != expected:
        raise ExportError(
            f"export row mismatch for {schema} {day} {part}: wrote {result.rows} != source {expected}"
        )
    footer = store.part_row_count(prefix, day, part)
    if footer != expected:
        raise ExportError(
            f"verify failed for {schema} {day} {part}: Parquet footer {footer} != source {expected}"
        )
    logger.info("Verified cold export: %s %s %s (%d rows)", schema, day.isoformat(), part, expected)
