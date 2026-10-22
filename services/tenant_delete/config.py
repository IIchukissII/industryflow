# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Environment configuration for the tenant-delete job (ADR-0033 dec 4)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class DbConfig:
    """Connection to TimescaleDB, over TLS (ADR-0017), as tenant_delete_user."""

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
class ColdConfig:
    """The cold layer and the job's list-and-delete principal on it (ADR-0025 dec 14)."""

    endpoint: str
    bucket: str
    region: str
    access_key: str
    secret_key: str


@dataclass(frozen=True)
class DeleteConfig:
    db: DbConfig
    redis_url: str
    # None where the deployment runs no cold layer: its step is then not offered, and a deletion
    # waits on it rather than recording a store it never looked at.
    cold: ColdConfig | None
    # How long the broker and the backups may still hold a deleted tenant's data: the deployment's
    # own retention settings, passed in where the job is deployed, so the record states the truth.
    broker_retention: timedelta
    backup_retention: timedelta


def _require(name: str) -> str:
    val = os.getenv(name)
    if not val:
        raise RuntimeError(f"required environment variable {name} is not set")
    return val


def _positive(name: str) -> int:
    value = int(_require(name))
    if value < 1:
        raise RuntimeError(f"{name} must be at least 1")
    return value


def load_config() -> DeleteConfig:
    """Build the job's config from the environment."""
    db = DbConfig(
        host=os.getenv("TIMESCALEDB_HOST", "timescaledb"),
        port=os.getenv("TIMESCALEDB_PORT", "5432"),
        dbname=os.getenv("TIMESCALEDB_DB", "industryflow"),
        user=os.getenv("TENANT_DELETE_DB_USER", "tenant_delete_user"),
        password=os.getenv("TENANT_DELETE_DB_PASSWORD"),
        # TLS to the DB (ADR-0017). libpq reads sslmode/sslrootcert directly.
        sslmode=os.getenv("DB_SSLMODE", "verify-full"),
        sslrootcert=os.getenv("DB_SSLROOTCERT", "/etc/ssl/industryflow/ca.crt"),
    )
    cold = None
    if os.getenv("TENANT_DELETE_COLD_BUCKET"):
        cold = ColdConfig(
            endpoint=_require("TENANT_DELETE_COLD_ENDPOINT"),
            bucket=_require("TENANT_DELETE_COLD_BUCKET"),
            region=os.getenv("TENANT_DELETE_COLD_REGION", "us-east-1"),
            access_key=_require("TENANT_DELETE_COLD_ACCESS_KEY"),
            secret_key=_require("TENANT_DELETE_COLD_SECRET_KEY"),
        )
    return DeleteConfig(
        db=db,
        redis_url=_require("TENANT_DELETE_REDIS_URL"),
        cold=cold,
        broker_retention=timedelta(hours=_positive("TENANT_DELETE_BROKER_RETENTION_HOURS")),
        backup_retention=timedelta(days=_positive("TENANT_DELETE_BACKUP_RETENTION_DAYS")),
    )
