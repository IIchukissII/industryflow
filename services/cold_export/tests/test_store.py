# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
The object-store adapter over a local directory: parts, footers, keys and the manifest round-trip.

`S3ColdStore` takes its filesystem as an argument, so the same code that talks to MinIO is driven
here against `pyarrow.fs.LocalFileSystem` — the part layout a reader depends on is checked without
an object store. The live S3 path is proven by test_cold_export_integration.py.
"""
from datetime import date, datetime, timedelta, timezone

import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import fs

from cold_export.config import StoreConfig
from cold_export.exporter import export_day, manifest_parts
from cold_export.naming import company_id_to_prefix, company_id_to_schema, day_partition
from cold_export.source import COLD_SCHEMA
from cold_export.store import S3ColdStore

from test_exporter import FakeSource  # noqa: E402 — the same source fake

CID = "22222222-2222-2222-2222-222222222222"
SCHEMA = company_id_to_schema(CID)
PREFIX = company_id_to_prefix(CID)
DAY = date(2026, 2, 3)


def _rows(n, start_minute=0):
    t0 = datetime(DAY.year, DAY.month, DAY.day, tzinfo=timezone.utc)
    return [{"time": t0 + timedelta(minutes=start_minute + i), "sensor_id": f"s-{i % 2}",
             "equipment_id": "eq-1", "site_id": "site", "value": float(i), "unit": "C",
             "quality_code": 1, "is_anomaly": False} for i in range(n)]


def _source():
    """The fake source, streaming batches in the exact COLD_SCHEMA the real source yields."""
    src = FakeSource()
    src.batch_schema = COLD_SCHEMA
    return src


def _store(tmp_path):
    cfg = StoreConfig(endpoint="unused", access_key=None, secret_key=None, region="us-east-1",
                      bucket=str(tmp_path))
    return S3ColdStore(cfg, filesystem=fs.LocalFileSystem())


def _mkdirs(tmp_path):
    (tmp_path / day_partition(PREFIX, DAY)).mkdir(parents=True, exist_ok=True)


def test_late_rows_land_in_a_second_file_and_the_first_is_untouched(tmp_path):
    _mkdirs(tmp_path)
    store = _store(tmp_path)
    src = _source()
    src.days[SCHEMA] = {DAY: _rows(4)}
    export_day(src, store, SCHEMA, PREFIX, DAY)
    first = tmp_path / day_partition(PREFIX, DAY) / "measurements.parquet"
    before = first.read_bytes()

    src.insert(SCHEMA, DAY, _rows(3, start_minute=100))
    assert export_day(src, store, SCHEMA, PREFIX, DAY) == "late_rows_exported"

    assert first.read_bytes() == before
    manifest = store.read_manifest(PREFIX, DAY)
    assert [p["file"] for p in manifest_parts(manifest)] == ["measurements.parquet",
                                                             "measurements-0001.parquet"]
    assert store.part_row_count(PREFIX, DAY, "measurements-0001.parquet") == 3
    # Every listed part reads back as the cold schema, and together they are the whole day.
    tables = [pq.read_table(tmp_path / day_partition(PREFIX, DAY) / p["file"])
              for p in manifest_parts(manifest)]
    assert all(t.schema.equals(COLD_SCHEMA) for t in tables)
    assert sum(t.num_rows for t in tables) == manifest["rows"] == 7


def test_part_keys_are_the_natural_key_columns(tmp_path):
    _mkdirs(tmp_path)
    store = _store(tmp_path)
    store.write_part(PREFIX, DAY, "measurements.parquet",
                     iter([pa.RecordBatch.from_pylist(_rows(2), schema=COLD_SCHEMA)]))
    keys = store.read_part_keys(PREFIX, DAY, "measurements.parquet")
    assert keys.column_names == ["time", "sensor_id"]
    assert keys.num_rows == 2
