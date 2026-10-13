# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Orchestration tests for the cold-layer exporter (ADR-0025 dec 3, dec 4).

The safety-critical properties are the ordering — no row is dropped without a verified, listed
export — and that a verified export is never destroyed. These drive `cold_export.exporter` against
in-memory fakes so every branch is checked without a database or object store: first export,
idempotent re-run, late rows after a drop (a new part, the archive untouched), a crash on either
side of the drop, an insert racing the export, and every verification failure. The fake source
models what cold_export_drop_day does: the drop compares the day with the exported snapshot and
drops nothing if any row arrived, changed or left in between.
"""
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pytest

from cold_export.exporter import export_day, export_tenant, manifest_parts, run_export  # noqa: E402
from cold_export.naming import company_id_to_prefix, company_id_to_schema  # noqa: E402
from cold_export.ports import ExportError, Tenant, WriteResult  # noqa: E402

DAY = date(2026, 1, 1)
SCHEMA_OF_ROW = pa.schema([
    ("time", pa.timestamp("us", tz="UTC")),
    ("sensor_id", pa.string()),
    ("equipment_id", pa.string()),
    ("value", pa.float64()),
])


def _rows(n, day=DAY, start_minute=0, sensor="s-1", equipment="eq-1"):
    t0 = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return [
        {"time": t0 + timedelta(minutes=start_minute + i), "sensor_id": sensor,
         "equipment_id": equipment, "value": float(start_minute + i)}
        for i in range(n)
    ]


def _batch(rows):
    return pa.RecordBatch.from_pylist(rows, schema=SCHEMA_OF_ROW)


class FakeSnapshot:
    def __init__(self, source, schema, day):
        self._source, self._schema, self._day = source, schema, day
        self._rows = [dict(r) for r in source.rows(schema, day)]   # the REPEATABLE READ snapshot
        self.rows = source.count_overrides.get((schema, day), len(self._rows))
        self.fingerprint = _fingerprint(self._rows)

    def batches(self):
        self._source.events.append("read")
        if self._source.during_read:
            self._source.during_read(self._schema, self._day)
        if self._rows:
            yield pa.RecordBatch.from_pylist(self._rows, schema=self._source.batch_schema)

    def drop(self, part):
        if self._source.crash_before_drop:
            # The process dies between the listed export and the drop's commit: nothing recorded.
            self._source.crash_before_drop -= 1
            raise SystemExit("crash before the drop committed")
        now = self._source.rows(self._schema, self._day)
        # cold_export_drop_day: under the lock, the day must still be exactly the exported snapshot.
        if len(now) != self.rows or _fingerprint(now) != self.fingerprint:
            self._source.events.append("drop-deferred")
            return False
        self._source.days[self._schema][self._day] = []
        self._source.ledger.add((self._schema, self._day, part))
        self._source.dropped.append((self._schema, self._day, part))
        self._source.events.append(f"drop:{part}")
        return True


def _fingerprint(rows):
    return str(sum(hash(tuple(sorted(r.items()))) for r in rows))


class FakeSource:
    def __init__(self):
        self.tenants = []
        self.days = {}               # schema -> {day: [row, ...]}
        self.count_overrides = {}    # (schema, day) -> forced snapshot count
        self.dropped = []            # (schema, day, part) in order
        self.ledger = set()          # committed drops (public.cold_export_drops)
        self.exportable_calls = []   # (schema, older_than)
        self.events = []
        self.during_read = None      # callback run while the export streams (a concurrent writer)
        self.batch_schema = SCHEMA_OF_ROW
        self.crash_before_drop = 0   # how many upcoming drops die before committing
        self.run_lock = {"held": False}  # the database's run-wide advisory lock

    def add_tenant(self, company_id, day_rows):
        schema = company_id_to_schema(company_id)
        self.tenants.append(Tenant(company_id=company_id, schema_name=schema))
        self.days[schema] = dict(day_rows)
        return schema

    def rows(self, schema, day):
        return self.days.get(schema, {}).get(day, [])

    def insert(self, schema, day, rows):
        self.days.setdefault(schema, {}).setdefault(day, []).extend(rows)

    def list_tenants(self):
        return list(self.tenants)

    def exportable_days(self, schema_name, older_than):
        self.exportable_calls.append((schema_name, older_than))
        return sorted(d for d, rows in self.days.get(schema_name, {}).items() if rows)

    @contextmanager
    def exclusive_run(self):
        if self.run_lock["held"]:
            yield False
            return
        self.run_lock["held"] = True
        try:
            yield True
        finally:
            self.run_lock["held"] = False

    @contextmanager
    def snapshot_day(self, schema_name, day):
        self.events.append("snapshot")
        try:
            yield FakeSnapshot(self, schema_name, day)
        finally:
            self.events.append("end")

    def drop_recorded(self, schema_name, day, part):
        return (schema_name, day, part) in self.ledger


class FakeStore:
    def __init__(self, source=None):
        self.objects = {}            # (prefix, day, part) -> pa.Table
        self.manifests = {}          # (prefix, day) -> manifest
        self.footer_overrides = {}   # (prefix, day, part) -> forced footer count
        self.writes = []             # (prefix, day, part, rows)
        self._events = source.events if source else []
        self.crash_before_manifest = 0  # how many upcoming manifest writes die before landing

    def read_manifest(self, prefix, day):
        m = self.manifests.get((prefix, day))
        return None if m is None else dict(m)

    def write_part(self, prefix, day, part, batches):
        table = pa.Table.from_batches(list(batches))
        self.objects[(prefix, day, part)] = table
        self.writes.append((prefix, day, part, table.num_rows))
        self._events.append(f"write:{part}")
        return WriteResult(rows=table.num_rows)

    def part_row_count(self, prefix, day, part):
        if (prefix, day, part) in self.footer_overrides:
            return self.footer_overrides[(prefix, day, part)]
        return self.objects[(prefix, day, part)].num_rows

    def read_part_keys(self, prefix, day, part):
        return self.objects[(prefix, day, part)].select(["time", "sensor_id"])

    def write_manifest(self, prefix, day, manifest):
        if self.crash_before_manifest:
            self.crash_before_manifest -= 1
            raise SystemExit("crash before the manifest was written")
        self.manifests[(prefix, day)] = dict(manifest)
        self._events.append("manifest")

    def archive(self, prefix, day):
        """Every row the day's manifest lists, as a reader sees the archive."""
        parts = manifest_parts(self.read_manifest(prefix, day))
        return pa.concat_tables([self.objects[(prefix, day, p["file"])] for p in parts]).to_pylist()


CID = "11111111-1111-1111-1111-111111111111"
SCHEMA = company_id_to_schema(CID)
PREFIX = company_id_to_prefix(CID)


def _setup(rows):
    src = FakeSource()
    src.days[SCHEMA] = {DAY: list(rows)}
    return src, FakeStore(src)


# --- happy path --------------------------------------------------------------------------------

def test_export_verify_list_then_drop():
    src, store = _setup(_rows(42))
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "exported"
    # Written, listed, and only then dropped.
    assert src.events == ["snapshot", "read", "write:measurements.parquet", "manifest",
                          "drop:measurements.parquet", "end"]
    assert store.manifests[(PREFIX, DAY)]["parts"] == [{"file": "measurements.parquet", "rows": 42}]
    assert store.manifests[(PREFIX, DAY)]["rows"] == 42
    assert src.rows(SCHEMA, DAY) == []


def test_empty_day_writes_nothing_and_drops_nothing():
    src, store = _setup([])
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "empty"
    assert store.writes == [] and src.dropped == []


def test_already_done_when_exported_and_dropped():
    src, store = _setup(_rows(5))
    export_day(src, store, SCHEMA, PREFIX, DAY)
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "already_done"
    assert len(store.writes) == 1 and len(src.dropped) == 1


# --- the archive is never destroyed (the regression this suite exists for) -----------------------

def test_late_rows_after_drop_are_added_as_a_new_part_and_the_archive_is_kept():
    # Day exported (5 rows) and dropped; 2 readings for that day arrive late, into a fresh chunk.
    original = _rows(5)
    src, store = _setup(original)
    export_day(src, store, SCHEMA, PREFIX, DAY)
    late = _rows(2, start_minute=600)
    src.insert(SCHEMA, DAY, late)

    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "late_rows_exported"

    # The first part is untouched and a second part holds exactly the late rows.
    assert store.objects[(PREFIX, DAY, "measurements.parquet")].num_rows == 5
    assert store.objects[(PREFIX, DAY, "measurements-0001.parquet")].num_rows == 2
    manifest = store.manifests[(PREFIX, DAY)]
    assert manifest["parts"] == [{"file": "measurements.parquet", "rows": 5},
                                 {"file": "measurements-0001.parquet", "rows": 2}]
    assert manifest["rows"] == 7
    # A reader of the manifest sees every row once: the history plus the late arrivals.
    assert sorted(r["time"] for r in store.archive(PREFIX, DAY)) == \
        sorted(r["time"] for r in original + late)
    assert src.rows(SCHEMA, DAY) == []


def test_late_rows_equal_in_number_to_the_archive_are_still_exported():
    # Row counts cannot tell "already exported" from "new": 5 late rows after a 5-row export must
    # still be exported, not dropped as if they were the archived ones.
    src, store = _setup(_rows(5))
    export_day(src, store, SCHEMA, PREFIX, DAY)
    src.insert(SCHEMA, DAY, _rows(5, start_minute=600))

    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "late_rows_exported"
    assert store.manifests[(PREFIX, DAY)]["rows"] == 10
    assert len(store.archive(PREFIX, DAY)) == 10


def test_redelivered_archived_reading_is_not_archived_twice():
    original = _rows(5)
    src, store = _setup(original)
    export_day(src, store, SCHEMA, PREFIX, DAY)
    # One reading arrives again after the drop (same natural key), with one genuinely new one.
    src.insert(SCHEMA, DAY, [dict(original[2]), *_rows(1, start_minute=700)])

    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "late_rows_exported"
    assert store.manifests[(PREFIX, DAY)]["parts"][1]["rows"] == 1
    assert len(store.archive(PREFIX, DAY)) == 6
    assert src.rows(SCHEMA, DAY) == []  # the duplicate is dropped too: it is archived


def test_late_part_keeps_the_within_file_sort():
    src, store = _setup(_rows(2))
    export_day(src, store, SCHEMA, PREFIX, DAY)
    src.insert(SCHEMA, DAY, _rows(2, start_minute=900, equipment="eq-9")
               + _rows(2, start_minute=800, equipment="eq-2"))
    export_day(src, store, SCHEMA, PREFIX, DAY)
    late = store.objects[(PREFIX, DAY, "measurements-0001.parquet")].to_pylist()
    assert [(r["equipment_id"], r["value"]) for r in late] == \
        [("eq-2", 800.0), ("eq-2", 801.0), ("eq-9", 900.0), ("eq-9", 901.0)]


def test_insert_racing_the_export_is_not_dropped_and_is_exported_next_run():
    # A row for the day arrives while the export runs. The drop sees the day differs from the
    # exported snapshot and drops nothing; the next run re-exports the pending part with the row.
    src, store = _setup(_rows(5))
    racing = _rows(1, start_minute=500)
    src.during_read = lambda schema, day: src.insert(schema, day, racing)

    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "drop_deferred"
    assert src.dropped == []
    assert len(src.rows(SCHEMA, DAY)) == 6                         # nothing lost
    assert store.manifests[(PREFIX, DAY)]["rows"] == 5              # the part is listed, pending

    src.during_read = None
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "reexported"
    assert store.manifests[(PREFIX, DAY)]["parts"] == [{"file": "measurements.parquet", "rows": 6}]
    assert len(store.archive(PREFIX, DAY)) == 6
    assert src.rows(SCHEMA, DAY) == []


def test_row_swapped_during_the_export_is_not_dropped():
    # One row deleted and another inserted while the export runs: the count is unchanged, so only
    # the fingerprint can tell. The new row must not be dropped unexported.
    rows = _rows(5)
    src, store = _setup(rows)

    def _swap(schema, day):
        src.days[schema][day] = src.days[schema][day][1:] + _rows(1, start_minute=500)

    src.during_read = _swap
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "drop_deferred"
    assert any(r["value"] == 500.0 for r in src.rows(SCHEMA, DAY))


# --- crash recovery (dec 4) --------------------------------------------------------------------

def test_crash_before_the_drop_committed_reexports_the_pending_part():
    # Part listed, then the run died before the drop committed: the rows are all still in the
    # source and the database has no record of the drop. Re-export under the same name.
    src, store = _setup(_rows(5))
    store.objects[(PREFIX, DAY, "measurements.parquet")] = pa.Table.from_batches([_batch(_rows(5))])
    store.manifests[(PREFIX, DAY)] = {"format": 2, "rows": 5,
                                      "parts": [{"file": "measurements.parquet", "rows": 5}]}
    src.insert(SCHEMA, DAY, _rows(1, start_minute=300))  # and a late row joined them meanwhile

    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "reexported"
    assert store.manifests[(PREFIX, DAY)]["parts"] == [{"file": "measurements.parquet", "rows": 6}]
    assert src.rows(SCHEMA, DAY) == []


def test_crash_after_the_drop_committed_keeps_the_part_and_appends():
    # The drop committed (the database recorded it) but the run died afterwards; then late rows
    # arrived. The listed part is committed history: it is kept and the late rows appended.
    src, store = _setup(_rows(5))
    export_day(src, store, SCHEMA, PREFIX, DAY)
    src.insert(SCHEMA, DAY, _rows(5, start_minute=600))

    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "late_rows_exported"
    assert store.objects[(PREFIX, DAY, "measurements.parquet")].num_rows == 5


def test_pending_part_whose_rows_were_deleted_is_kept_and_recorded():
    src, store = _setup([])
    store.objects[(PREFIX, DAY, "measurements.parquet")] = pa.Table.from_batches([_batch(_rows(5))])
    store.manifests[(PREFIX, DAY)] = {"format": 2, "rows": 5,
                                      "parts": [{"file": "measurements.parquet", "rows": 5}]}
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "resumed_drop"
    assert (SCHEMA, DAY, "measurements.parquet") in src.ledger
    assert len(store.archive(PREFIX, DAY)) == 5


def test_legacy_manifest_crash_before_drop_resumes_without_rewriting():
    # A single-file manifest from before parts existed, and its drop never ran: every source row is
    # already archived, so nothing is written — the day is dropped and the manifest upgraded.
    rows = _rows(5)
    src, store = _setup(rows)
    store.objects[(PREFIX, DAY, "measurements.parquet")] = pa.Table.from_batches([_batch(rows)])
    store.manifests[(PREFIX, DAY)] = {"schema": SCHEMA, "day": DAY.isoformat(), "rows": 5}

    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "resumed_drop"
    assert store.writes == []
    assert store.manifests[(PREFIX, DAY)]["parts"] == [{"file": "measurements.parquet", "rows": 5}]
    assert src.rows(SCHEMA, DAY) == []


def test_legacy_manifest_late_rows_are_appended():
    rows = _rows(5)
    src, store = _setup(_rows(3, start_minute=600))
    store.objects[(PREFIX, DAY, "measurements.parquet")] = pa.Table.from_batches([_batch(rows)])
    store.manifests[(PREFIX, DAY)] = {"schema": SCHEMA, "day": DAY.isoformat(), "rows": 5}

    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "late_rows_exported"
    assert store.objects[(PREFIX, DAY, "measurements.parquet")].num_rows == 5
    assert store.manifests[(PREFIX, DAY)]["rows"] == 8


def _archive_keys(store):
    return [(r["time"], r["sensor_id"]) for r in store.archive(PREFIX, DAY)]


def _export_crashing(src, store):
    with pytest.raises(SystemExit):
        export_day(src, store, SCHEMA, PREFIX, DAY)


def test_crash_before_a_late_parts_drop_never_duplicates_rows_across_parts():
    # Day exported and dropped (part 0, committed). Late rows arrive and are exported as part 1,
    # listed, and then the run dies before the drop commits — twice, with more late rows arriving in
    # between. Part 1's rows are still in the source each time; exporting them again as a further
    # part would archive them twice. Part 1 is re-exported in place instead.
    original, late, later = _rows(5), _rows(3, start_minute=600), _rows(2, start_minute=900)
    src, store = _setup(original)
    export_day(src, store, SCHEMA, PREFIX, DAY)
    src.insert(SCHEMA, DAY, late)
    src.crash_before_drop = 1
    _export_crashing(src, store)
    assert [p["file"] for p in manifest_parts(store.read_manifest(PREFIX, DAY))] == \
        ["measurements.parquet", "measurements-0001.parquet"]

    src.insert(SCHEMA, DAY, later)
    src.crash_before_drop = 1
    _export_crashing(src, store)

    outcome = export_day(src, store, SCHEMA, PREFIX, DAY)
    keys = _archive_keys(store)
    assert len(keys) == len(set(keys)) == 10
    assert sorted(keys) == sorted((r["time"], r["sensor_id"]) for r in original + late + later)
    assert outcome == "reexported"
    assert store.manifests[(PREFIX, DAY)]["parts"] == [{"file": "measurements.parquet", "rows": 5},
                                                       {"file": "measurements-0001.parquet", "rows": 5}]
    assert store.objects[(PREFIX, DAY, "measurements.parquet")].num_rows == 5  # history untouched
    assert src.rows(SCHEMA, DAY) == []
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "already_done"


def test_crash_before_the_first_parts_drop_then_late_rows_never_duplicates_rows():
    # The first export dies before its drop commits; late rows join the day before the next run.
    # The unrecorded first part is rewritten in place with the whole day, not followed by a part
    # that repeats its rows.
    src, store = _setup(_rows(5))
    src.crash_before_drop = 1
    _export_crashing(src, store)
    src.insert(SCHEMA, DAY, _rows(2, start_minute=600))

    outcome = export_day(src, store, SCHEMA, PREFIX, DAY)
    keys = _archive_keys(store)
    assert len(keys) == len(set(keys)) == 7
    assert outcome == "reexported"
    assert store.manifests[(PREFIX, DAY)]["parts"] == [{"file": "measurements.parquet", "rows": 7}]


def test_crash_between_writing_a_part_and_listing_it_never_duplicates_rows():
    # The late part is written but the run dies before the manifest lists it: an unlisted object no
    # reader reads. The next run writes the same part name again, so the archive holds each row once.
    src, store = _setup(_rows(5))
    export_day(src, store, SCHEMA, PREFIX, DAY)
    src.insert(SCHEMA, DAY, _rows(3, start_minute=600))
    store.crash_before_manifest = 1
    _export_crashing(src, store)
    assert len(manifest_parts(store.read_manifest(PREFIX, DAY))) == 1

    outcome = export_day(src, store, SCHEMA, PREFIX, DAY)
    keys = _archive_keys(store)
    assert len(keys) == len(set(keys)) == 8
    assert outcome == "late_rows_exported"
    assert [w[2] for w in store.writes] == ["measurements.parquet", "measurements-0001.parquet",
                                            "measurements-0001.parquet"]


# --- verification failures MUST block the drop (dec 3) ------------------------------------------

def test_writer_count_mismatch_blocks_drop():
    # The lock counted 10 rows but only 6 stream out: the write count check fails.
    src, store = _setup(_rows(6))
    src.count_overrides[(SCHEMA, DAY)] = 10
    with pytest.raises(ExportError):
        export_day(src, store, SCHEMA, PREFIX, DAY)
    assert src.dropped == []
    assert store.manifests == {}                      # not listed


def test_footer_mismatch_blocks_drop():
    # The written object's Parquet footer disagrees with the source (truncated upload).
    src, store = _setup(_rows(42))
    store.footer_overrides[(PREFIX, DAY, "measurements.parquet")] = 40
    with pytest.raises(ExportError):
        export_day(src, store, SCHEMA, PREFIX, DAY)
    assert src.dropped == []
    assert store.manifests == {}


def test_corrupt_committed_part_blocks_everything():
    # A committed part's footer no longer matches the manifest — refuse to trust the day at all.
    src, store = _setup(_rows(5))
    export_day(src, store, SCHEMA, PREFIX, DAY)
    src.insert(SCHEMA, DAY, _rows(2, start_minute=600))
    store.footer_overrides[(PREFIX, DAY, "measurements.parquet")] = 4
    with pytest.raises(ExportError, match="corrupt"):
        export_day(src, store, SCHEMA, PREFIX, DAY)
    assert len(src.rows(SCHEMA, DAY)) == 2            # late rows not dropped


def test_late_part_footer_mismatch_keeps_late_rows_and_archive():
    src, store = _setup(_rows(5))
    export_day(src, store, SCHEMA, PREFIX, DAY)
    src.insert(SCHEMA, DAY, _rows(2, start_minute=600))
    store.footer_overrides[(PREFIX, DAY, "measurements-0001.parquet")] = 1
    with pytest.raises(ExportError):
        export_day(src, store, SCHEMA, PREFIX, DAY)
    assert len(src.rows(SCHEMA, DAY)) == 2
    assert store.manifests[(PREFIX, DAY)]["parts"] == [{"file": "measurements.parquet", "rows": 5}]


# --- multi-day + tenant isolation --------------------------------------------------------------

def test_days_exported_oldest_first():
    d1, d2, d3 = date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)
    src = FakeSource()
    src.add_tenant(CID, {d3: _rows(3, day=d3), d1: _rows(1, day=d1), d2: _rows(2, day=d2)})
    store = FakeStore(src)
    export_tenant(src, store, Tenant(CID, SCHEMA), cutoff=date(2026, 2, 1))
    assert [(s, d) for s, d, _ in src.dropped] == [(SCHEMA, d1), (SCHEMA, d2), (SCHEMA, d3)]


def test_tenant_isolation_each_writes_only_its_own_prefix():
    cid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    cid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    src = FakeSource()
    src.add_tenant(cid_a, {DAY: _rows(5)})
    src.add_tenant(cid_b, {DAY: _rows(9)})
    store = FakeStore(src)
    run_export(src, store, horizon_days=30, today=date(2026, 3, 1))

    prefix_a, prefix_b = company_id_to_prefix(cid_a), company_id_to_prefix(cid_b)
    assert {p for (p, _, _, _) in store.writes} == {prefix_a, prefix_b}
    assert store.manifests[(prefix_a, DAY)]["rows"] == 5
    assert store.manifests[(prefix_b, DAY)]["rows"] == 9


def test_schema_is_derived_from_company_id_not_trusted_from_the_registry():
    # The registry row names another tenant's schema. The exporter must neither read nor drop it.
    victim = company_id_to_schema("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
    src = FakeSource()
    src.days[victim] = {DAY: _rows(5)}
    store = FakeStore(src)
    with pytest.raises(ExportError, match="canonical schema"):
        export_tenant(src, store, Tenant(CID, victim), cutoff=date(2026, 2, 1))
    assert src.exportable_calls == [] and src.dropped == [] and store.writes == []


def test_registry_schema_name_that_is_not_an_identifier_is_refused():
    src = FakeSource()
    store = FakeStore(src)
    with pytest.raises(ExportError):
        export_tenant(src, store, Tenant(CID, 'x"; DROP SCHEMA public; --'), cutoff=date(2026, 2, 1))
    assert src.exportable_calls == []


# --- run-level behaviour -----------------------------------------------------------------------

def test_run_export_applies_horizon_cutoff():
    src = FakeSource(); src.add_tenant(CID, {})
    store = FakeStore(src)
    run_export(src, store, horizon_days=30, today=date(2026, 3, 31))
    # cutoff = today - 30d = 2026-03-01; exportable_days is queried with exactly that.
    assert src.exportable_calls == [(SCHEMA, date(2026, 3, 1))]


def test_run_export_isolates_tenant_failure_but_still_fails_run():
    # Tenant A fails verification; tenant B must still export and drop, but the run reports failure.
    cid_a = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    cid_b = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    src = FakeSource()
    schema_a = src.add_tenant(cid_a, {DAY: _rows(6)})
    src.count_overrides[(schema_a, DAY)] = 10
    schema_b = src.add_tenant(cid_b, {DAY: _rows(9)})
    store = FakeStore(src)
    with pytest.raises(ExportError):
        run_export(src, store, horizon_days=30, today=date(2026, 3, 1))
    # B succeeded despite A's failure; A dropped nothing.
    assert (schema_b, DAY, "measurements.parquet") in src.dropped
    assert all(schema != schema_a for schema, _, _ in src.dropped)


# --- runs never overlap (dec 4: a committed part is never written again) ------------------------

def test_overlapping_run_exports_nothing_and_does_not_fail():
    src, store = _setup(_rows(5))
    src.tenants.append(Tenant(company_id=CID, schema_name=SCHEMA))
    src.run_lock["held"] = True  # another run is in progress
    assert run_export(src, store, horizon_days=30, today=date(2026, 3, 1)) == \
        {"skipped_overlapping_run": 1}
    assert src.events == [] and store.writes == [] and src.dropped == []
    assert len(src.rows(SCHEMA, DAY)) == 5


def test_run_started_during_another_never_rewrites_its_committed_part():
    # A second run starts while the first is streaming the day. Unlocked, it would export and drop
    # the day, and the first would then write the same part name over the now-committed part.
    src, store = _setup(_rows(5))
    src.tenants.append(Tenant(company_id=CID, schema_name=SCHEMA))
    nested = []

    def _second_run(schema, day):
        src.during_read = None
        nested.append(run_export(src, store, horizon_days=30, today=date(2026, 3, 1)))

    src.during_read = _second_run
    run_export(src, store, horizon_days=30, today=date(2026, 3, 1))

    assert [w[2] for w in store.writes] == ["measurements.parquet"]
    assert nested == [{"skipped_overlapping_run": 1}]
    assert src.dropped == [(SCHEMA, DAY, "measurements.parquet")]


def test_run_lock_is_released_after_a_run_even_a_failed_one():
    src, store = _setup(_rows(6))
    src.tenants.append(Tenant(company_id=CID, schema_name=SCHEMA))
    src.count_overrides[(SCHEMA, DAY)] = 10
    with pytest.raises(ExportError):
        run_export(src, store, horizon_days=30, today=date(2026, 3, 1))
    assert src.run_lock["held"] is False
    del src.count_overrides[(SCHEMA, DAY)]
    assert run_export(src, store, horizon_days=30, today=date(2026, 3, 1)) == {"exported": 1}
