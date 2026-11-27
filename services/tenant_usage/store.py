# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The object store, as the job's principal sees it: list, and nothing else (ADR-0049 dec 11, 13)."""
from __future__ import annotations

from typing import Iterator

import boto3


class S3Listing:
    def __init__(self, endpoint: str, region: str, access_key: str | None, secret_key: str | None):
        self._client = boto3.client("s3", endpoint_url=endpoint, region_name=region,
                                    aws_access_key_id=access_key, aws_secret_access_key=secret_key)

    def list_objects(self, bucket: str, prefix: str) -> Iterator[tuple[str, int]]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                yield obj["Key"], obj["Size"]
