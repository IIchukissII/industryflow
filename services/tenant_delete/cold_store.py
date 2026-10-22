# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Removes a deleted tenant's raw history from the cold layer (ADR-0025 dec 14, ADR-0033 dec 4).

The principal can list and delete under tenant prefixes and nothing else: it never reads an object,
and the exporter, the cold layer's one writer, cannot delete. The prefix is the shared contract's,
with its separator, so one tenant's prefix never matches a longer sibling. The backup's copy keeps
the objects one backup retention period longer (ADR-0032 dec 4).
"""
from __future__ import annotations

from industryflow_contracts import tenancy

from .job import Removal

_BATCH = 1000  # the most keys one DeleteObjects request takes


class ColdStore:
    def __init__(self, client, bucket: str):
        self._client = client
        self._bucket = bucket

    def purge(self, company_id: str, user_ids: list) -> Removal:
        prefix = tenancy.cold_object_prefix(company_id) + "/"
        removed, previous = 0, None
        # Listing again after each batch, not paginating one listing: the deletes change what a
        # continuation token would point at.
        while True:
            page = self._client.list_objects_v2(Bucket=self._bucket, Prefix=prefix, MaxKeys=_BATCH)
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if not keys:
                break
            if keys == previous:
                raise RuntimeError(f"cold layer kept {len(keys)} object(s) it reported deleted under {prefix}")
            previous = keys
            result = self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys, "Quiet": True})
            errors = result.get("Errors") or []
            if errors:
                raise RuntimeError(f"cold layer refused {len(errors)} deletion(s), first: {errors[0]}")
            removed += len(keys)
        return Removal(detail={"bucket": self._bucket, "prefix": prefix, "objects": removed})
