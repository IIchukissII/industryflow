# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Object-store adapter for the cold-layer exporter (ADR-0025 dec 10, dec 11).

Writes each day's measurements as a single partitioned Parquet file plus a JSON manifest, using
the exporter's write-scoped principal. Verification reads the Parquet footer back from the store
(the copy that will actually be kept), not an in-memory number — so a truncated or corrupt
upload is caught before the source chunk is dropped.
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Iterator, Optional

import pyarrow as pa
import pyarrow.parquet as pq
from pyarrow import fs

from .config import StoreConfig
from .naming import manifest_key, parquet_key
from .ports import WriteResult

logger = logging.getLogger(__name__)


def _split_endpoint(endpoint: str) -> tuple[str, str]:
    """Turn ``http://minio:9000`` into (``minio:9000``, ``http``) for S3FileSystem."""
    if "://" in endpoint:
        scheme, host = endpoint.split("://", 1)
    else:
        scheme, host = "https", endpoint
    return host, scheme


class S3ColdStore:
    """Concrete ColdStore over pyarrow.fs.S3FileSystem (see ports.ColdStore)."""

    def __init__(self, cfg: StoreConfig):
        host, scheme = _split_endpoint(cfg.endpoint)
        self._bucket = cfg.bucket
        # pyarrow's S3FileSystem uses path-style addressing by default when endpoint_override is
        # set (MinIO does not do virtual-host buckets), which is exactly what we need. (The
        # explicit force_virtual_addressing knob only exists in pyarrow >= 15; we pin 14.)
        self._fs = fs.S3FileSystem(
            access_key=cfg.access_key,
            secret_key=cfg.secret_key,
            endpoint_override=host,
            scheme=scheme,
            region=cfg.region,
        )

    def _path(self, key: str) -> str:
        return f"{self._bucket}/{key}"

    def read_manifest(self, prefix: str, day: date) -> Optional[dict]:
        path = self._path(manifest_key(prefix, day))
        info = self._fs.get_file_info(path)
        if info.type == fs.FileType.NotFound:
            return None
        with self._fs.open_input_file(path) as f:
            return json.loads(f.read().decode("utf-8"))

    def write_parquet(self, prefix: str, day: date, batches: Iterator[pa.RecordBatch]) -> WriteResult:
        path = self._path(parquet_key(prefix, day))
        it = iter(batches)
        try:
            first = next(it)
        except StopIteration:
            # export_day only writes when the source day is non-empty, so this is defensive.
            raise ValueError(f"no rows to write for {prefix} {day.isoformat()}")
        rows = 0
        with self._fs.open_output_stream(path) as sink:
            writer = pq.ParquetWriter(sink, first.schema, compression="zstd")
            try:
                writer.write_batch(first)
                rows += first.num_rows
                for batch in it:
                    writer.write_batch(batch)
                    rows += batch.num_rows
            finally:
                writer.close()
        logger.info("Wrote cold Parquet: %s (%d rows)", path, rows)
        return WriteResult(rows=rows)

    def parquet_row_count(self, prefix: str, day: date) -> int:
        path = self._path(parquet_key(prefix, day))
        with self._fs.open_input_file(path) as f:
            return pq.ParquetFile(f).metadata.num_rows

    def write_manifest(self, prefix: str, day: date, manifest: dict) -> None:
        path = self._path(manifest_key(prefix, day))
        with self._fs.open_output_stream(path) as sink:
            sink.write(json.dumps(manifest, sort_keys=True).encode("utf-8"))
