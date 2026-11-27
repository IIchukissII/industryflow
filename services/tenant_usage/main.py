# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Entrypoint of the tenant storage measurement job (ADR-0049 dec 11): one run over every tenant, on a
schedule. A non-zero exit marks the run failed; a tenant it could not measure keeps its last measurement,
whose age alerts the operator."""
from __future__ import annotations

import logging
import os
import sys

from .measure import buckets, run
from .source import PostgresDatabase
from .store import S3Listing

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("tenant_usage")


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"required environment variable {name} is not set")
    return value


def main() -> int:
    dsn = {
        "host": os.getenv("TIMESCALEDB_HOST", "timescaledb"),
        "port": os.getenv("TIMESCALEDB_PORT", "5432"),
        "dbname": os.getenv("TIMESCALEDB_DB", "industryflow"),
        "user": os.getenv("TENANT_USAGE_DB_USER", "tenant_usage_user"),
        "password": _require("TENANT_USAGE_DB_PASSWORD"),
        # TLS to the database (ADR-0017); libpq reads both.
        "sslmode": os.getenv("DB_SSLMODE", "verify-full"),
        "sslrootcert": os.getenv("DB_SSLROOTCERT", "/etc/ssl/industryflow/ca.crt"),
    }
    store = S3Listing(endpoint=os.getenv("TENANT_USAGE_STORE_ENDPOINT", "http://minio:9000"),
                      region=os.getenv("TENANT_USAGE_STORE_REGION", "us-east-1"),
                      access_key=_require("TENANT_USAGE_STORE_ACCESS_KEY"),
                      secret_key=_require("TENANT_USAGE_STORE_SECRET_KEY"))
    # A bucket the deployment does not run is left out; one it runs but this job is not told of would
    # leave that store's data uncounted, so both deployments pass every bucket they create.
    where = buckets(os.getenv("COLD_STORE_BUCKET"), os.getenv("TRACKING_ARTIFACT_BUCKET"))
    db = PostgresDatabase(dsn)
    try:
        failed = run(db, store, where)
    except Exception:  # noqa: BLE001 - top level: log and fail the run
        logger.exception("tenant storage measurement failed")
        return 1
    finally:
        db.close()
    if failed:
        logger.error("%d tenant(s) not measured in the object store", failed)
        return 1
    logger.info("tenant storage measured")
    return 0


if __name__ == "__main__":
    sys.exit(main())
