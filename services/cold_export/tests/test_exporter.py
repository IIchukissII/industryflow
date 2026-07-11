# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Orchestration tests for the cold-layer exporter (ADR-0025 dec 3, dec 4).

The safety-critical property is the ordering: no chunk is dropped without a verified export.
These drive `cold_export.exporter` against in-memory fakes so every branch — first export,
idempotent skip, crash-resume, late-data re-export, and every verification failure — is checked
without any database or object store. The fakes let a test force a count/footer mismatch and
then assert that `drop_day` was never reached.
"""
from datetime import date

import pyarrow as pa
import pytest

from cold_export.exporter import export_day, export_tenant, run_export  # noqa: E402
from cold_export.naming import company_id_to_prefix  # noqa: E402
from cold_export.ports import ExportError, Tenant, WriteResult  # noqa: E402

DAY = date(2026, 1, 1)


def _batch(n):
    return pa.record_batch([pa.array(list(range(n)))], names=["x"])


class FakeSource:
    def __init__(self):
        self.tenants = []
        self.days = {}               # schema -> {day: actual_rows}
        self.count_overrides = {}    # (schema, day) -> forced count
        self.dropped = []            # (schema, day) recorded in order
        self.exportable_calls = []   # (schema, older_than)

    def add_tenant(self, company_id, schema, day_rows):
        self.tenants.append(Tenant(company_id=company_id, schema_name=schema))
        self.days[schema] = dict(day_rows)

    def list_tenants(self):
        return list(self.tenants)

    def exportable_days(self, schema_name, older_than):
        self.exportable_calls.append((schema_name, older_than))
        return sorted(self.days.get(schema_name, {}))

    def count_day(self, schema_name, day):
        if (schema_name, day) in self.count_overrides:
            return self.count_overrides[(schema_name, day)]
        return self.days.get(schema_name, {}).get(day, 0)

    def read_day(self, schema_name, day):
        n = self.days.get(schema_name, {}).get(day, 0)
        if n:
            yield _batch(n)

    def drop_day(self, schema_name, day):
        self.dropped.append((schema_name, day))
        self.days.get(schema_name, {}).pop(day, None)


class FakeStore:
    def __init__(self):
        self.objects = {}            # (prefix, day) -> {"rows": n, "manifest": {...}}
        self.footer_overrides = {}   # (prefix, day) -> forced footer count
        self.writes = []             # (prefix, day, rows)
        self.manifests = []          # (prefix, day)

    def seed_manifest(self, prefix, day, rows):
        self.objects[(prefix, day)] = {"rows": rows, "manifest": {"rows": rows}}

    def read_manifest(self, prefix, day):
        obj = self.objects.get((prefix, day))
        return dict(obj["manifest"]) if obj and "manifest" in obj else None

    def write_parquet(self, prefix, day, batches):
        rows = sum(b.num_rows for b in batches)
        self.objects.setdefault((prefix, day), {})["rows"] = rows
        self.writes.append((prefix, day, rows))
        return WriteResult(rows=rows)

    def parquet_row_count(self, prefix, day):
        if (prefix, day) in self.footer_overrides:
            return self.footer_overrides[(prefix, day)]
        return self.objects[(prefix, day)]["rows"]

    def write_manifest(self, prefix, day, manifest):
        self.objects.setdefault((prefix, day), {})["manifest"] = dict(manifest)
        self.manifests.append((prefix, day))


CID = "11111111-1111-1111-1111-111111111111"
SCHEMA = "tenant_11111111_1111_1111_1111_111111111111"
PREFIX = company_id_to_prefix(CID)


# --- happy path --------------------------------------------------------------------------------

def test_export_then_verify_then_drop():
    src = FakeSource(); src.days[SCHEMA] = {DAY: 42}
    store = FakeStore()
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "exported"
    # A verified manifest was written, and only then the chunk was dropped.
    assert store.writes == [(PREFIX, DAY, 42)]
    assert store.manifests == [(PREFIX, DAY)]
    assert src.dropped == [(SCHEMA, DAY)]


def test_empty_day_writes_nothing_and_drops_nothing():
    src = FakeSource(); src.days[SCHEMA] = {DAY: 0}
    store = FakeStore()
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "empty"
    assert store.writes == [] and src.dropped == []


# --- idempotency / self-healing (dec 4) --------------------------------------------------------

def test_already_done_when_manifest_exists_and_source_gone():
    # A previous run exported, verified, and dropped. Re-run must be a no-op.
    src = FakeSource(); src.days[SCHEMA] = {}          # chunk already dropped
    store = FakeStore(); store.seed_manifest(PREFIX, DAY, 42)
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "already_done"
    assert store.writes == [] and src.dropped == []


def test_resume_drop_after_crash_between_verify_and_drop():
    # Manifest present and source still matches it: crash happened before the drop. Resume the
    # drop WITHOUT re-writing the Parquet.
    src = FakeSource(); src.days[SCHEMA] = {DAY: 42}
    store = FakeStore(); store.seed_manifest(PREFIX, DAY, 42)
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "resumed_drop"
    assert store.writes == []                          # not re-exported
    assert src.dropped == [(SCHEMA, DAY)]


def test_reexport_when_late_data_landed():
    # Manifest says 5 rows; source now has 7 (late arrivals). Re-export to capture them, then drop.
    src = FakeSource(); src.days[SCHEMA] = {DAY: 7}
    store = FakeStore(); store.seed_manifest(PREFIX, DAY, 5)
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "reexported"
    assert store.writes == [(PREFIX, DAY, 7)]
    assert store.objects[(PREFIX, DAY)]["manifest"]["rows"] == 7
    assert src.dropped == [(SCHEMA, DAY)]


# --- verification failures MUST block the drop (dec 3) ------------------------------------------

def test_writer_count_mismatch_blocks_drop():
    # Source claims 10 rows but only 6 stream out (a read bug/race): the write count check fails.
    src = FakeSource(); src.days[SCHEMA] = {DAY: 6}
    src.count_overrides[(SCHEMA, DAY)] = 10
    store = FakeStore()
    with pytest.raises(ExportError):
        export_day(src, store, SCHEMA, PREFIX, DAY)
    assert src.dropped == []                           # nothing dropped
    assert store.manifests == []                       # not marked verified


def test_footer_mismatch_blocks_drop():
    # The written object's Parquet footer disagrees with the source (truncated upload).
    src = FakeSource(); src.days[SCHEMA] = {DAY: 42}
    store = FakeStore(); store.footer_overrides[(PREFIX, DAY)] = 40
    with pytest.raises(ExportError):
        export_day(src, store, SCHEMA, PREFIX, DAY)
    assert src.dropped == []
    assert store.manifests == []


def test_corrupt_existing_export_blocks_drop():
    # Manifest says 42 but the stored Parquet footer reads 40 — refuse to trust it, do not drop.
    src = FakeSource(); src.days[SCHEMA] = {DAY: 42}
    store = FakeStore(); store.seed_manifest(PREFIX, DAY, 42)
    store.footer_overrides[(PREFIX, DAY)] = 40
    with pytest.raises(ExportError):
        export_day(src, store, SCHEMA, PREFIX, DAY)
    assert src.dropped == []


# --- multi-day + tenant isolation --------------------------------------------------------------

def test_days_exported_oldest_first():
    d1, d2, d3 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)
    src = FakeSource(); src.add_tenant(CID, SCHEMA, {d3: 3, d1: 1, d2: 2})
    store = FakeStore()
    export_tenant(src, store, Tenant(CID, SCHEMA), cutoff=date(2026, 2, 1))
    assert src.dropped == [(SCHEMA, d1), (SCHEMA, d2), (SCHEMA, d3)]


def test_tenant_isolation_each_writes_only_its_own_prefix():
    cid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    cid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    src = FakeSource()
    src.add_tenant(cid_a, "tenant_a", {DAY: 5})
    src.add_tenant(cid_b, "tenant_b", {DAY: 9})
    store = FakeStore()
    run_export(src, store, horizon_days=30, today=date(2026, 3, 1))

    prefix_a, prefix_b = company_id_to_prefix(cid_a), company_id_to_prefix(cid_b)
    written_prefixes = {p for (p, _, _) in store.writes}
    assert written_prefixes == {prefix_a, prefix_b}
    # Tenant A's rows live only under A's prefix; B's only under B's. No shared/foreign path.
    assert store.objects[(prefix_a, DAY)]["rows"] == 5
    assert store.objects[(prefix_b, DAY)]["rows"] == 9
    assert (prefix_a, DAY) != (prefix_b, DAY)


# --- run-level behaviour -----------------------------------------------------------------------

def test_run_export_applies_horizon_cutoff():
    src = FakeSource(); src.add_tenant(CID, SCHEMA, {})
    store = FakeStore()
    run_export(src, store, horizon_days=30, today=date(2026, 3, 31))
    # cutoff = today - 30d = 2026-03-01; exportable_days is queried with exactly that.
    assert src.exportable_calls == [(SCHEMA, date(2026, 3, 1))]


def test_run_export_isolates_tenant_failure_but_still_fails_run():
    # Tenant A fails verification; tenant B must still export and drop, but the run reports failure.
    cid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    cid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    src = FakeSource()
    src.add_tenant(cid_a, "tenant_a", {DAY: 6}); src.count_overrides[("tenant_a", DAY)] = 10
    src.add_tenant(cid_b, "tenant_b", {DAY: 9})
    store = FakeStore()
    with pytest.raises(ExportError):
        run_export(src, store, horizon_days=30, today=date(2026, 3, 1))
    # B succeeded despite A's failure; A dropped nothing.
    assert ("tenant_b", DAY) in src.dropped
    assert all(schema != "tenant_a" for schema, _ in src.dropped)
