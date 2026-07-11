# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Environment configuration for the cold-layer exporter (ADR-0025)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DbConfig:
    """Connection to TimescaleDB, over TLS (ADR-0017), as the write-scoped cold_export_user."""

    host: str
    port: str
    dbname: str
    user: str
    password: str | None
    sslmode: str
    sslrootcert: str

    def dsn_kwargs(self) -> dict:
        cfg = {
            "host": self.host,
            "port": self.port,
            "dbname": self.dbname,
            "user": self.user,
            "password": self.password,
            "sslmode": self.sslmode,
        }
        if self.sslrootcert:
            cfg["sslrootcert"] = self.sslrootcert
        return cfg


@dataclass(frozen=True)
class StoreConfig:
    """
    Object store for the cold layer (ADR-0025 dec 10, dec 11).

    The credentials here are the exporter's *write-scoped* principal — scoped to the cold
    bucket/prefixes only. It is a distinct MinIO/S3 identity from the streaming Spark's
    checkpoint credentials and from the (non-durable) read path.
    """

    endpoint: str
    access_key: str | None
    secret_key: str | None
    region: str
    bucket: str


@dataclass(frozen=True)
class ExportConfig:
    db: DbConfig
    store: StoreConfig
    horizon_days: int


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"required environment variable {name} is not set")
    return val


def load_config() -> ExportConfig:
    """Build the export config from the environment."""
    db = DbConfig(
        host=os.getenv("TIMESCALEDB_HOST", "timescaledb"),
        port=os.getenv("TIMESCALEDB_PORT", "5432"),
        dbname=os.getenv("TIMESCALEDB_DB", "industryflow"),
        user=os.getenv("COLD_EXPORT_DB_USER", "cold_export_user"),
        password=os.getenv("COLD_EXPORT_DB_PASSWORD"),
        # TLS to the DB (ADR-0017). libpq reads sslmode/sslrootcert directly.
        sslmode=os.getenv("DB_SSLMODE", "verify-full"),
        sslrootcert=os.getenv("DB_SSLROOTCERT", "/etc/ssl/industryflow/ca.crt"),
    )
    store = StoreConfig(
        endpoint=os.getenv("COLD_STORE_ENDPOINT", os.getenv("MINIO_ENDPOINT", "http://minio:9000")),
        access_key=os.getenv("COLD_STORE_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID"),
        secret_key=os.getenv("COLD_STORE_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY"),
        region=os.getenv("COLD_STORE_REGION", "us-east-1"),
        bucket=_require("COLD_STORE_BUCKET"),
    )
    horizon_days = int(os.getenv("COLD_EXPORT_HORIZON_DAYS", "30"))
    if horizon_days < 1:
        raise RuntimeError(f"COLD_EXPORT_HORIZON_DAYS must be >= 1, got {horizon_days}")
    return ExportConfig(db=db, store=store, horizon_days=horizon_days)
