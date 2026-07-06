# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Aggregate-backed baseline provider (ADR-0023 rev 1).

The windowed baseline a feature transform needs — the recent rolling mean of a sensor — is
already materialized by the Spark aggregation job as ``sensor_aggregations_<granularity>``
(ADR-0006). This provider reads that materialized ``avg_value`` from the tenant schema instead
of computing a rolling mean over a Redis ring. It is the seam that lets the Redis feature store
stop being a windowing substrate (ADR-0023 dec 2 / dec 7).

A short in-process TTL cache absorbs the per-feature reads of a single ``/predict`` (a model
may carry many statistical features): the aggregates only change when a window closes (order of
a minute), so a few seconds of caching adds negligible staleness while collapsing N queries.
"""
import logging
import os
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)

# Aggregate granularities the Spark job materializes (ADR-0006). Allow-listed because the
# granularity selects a TABLE NAME, which cannot be a bound query parameter — this is the
# injection guard for that interpolation.
_AGG_TABLES = {
    "1min": "sensor_aggregations_1min",
    "5min": "sensor_aggregations_5min",
    "1hour": "sensor_aggregations_1hour",
}
DEFAULT_GRANULARITY = "1min"


def _schema_for(company_id: str) -> str:
    """Tenant schema name for a company_id, validated as a UUID before interpolation (ADR-0003).

    Mirrors repository.normalize_company_id_to_schema; kept local so the provider has no import
    coupling to the repository layer. A non-UUID raises ValueError rather than yielding an
    arbitrary schema name.
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


class AggregateBaselineProvider:
    """Reads a sensor's latest windowed mean from the tenant's Spark-materialized aggregates."""

    def __init__(self, pool, cache_ttl_seconds: Optional[float] = None,
                 query_timeout_seconds: Optional[float] = None):
        """
        Args:
            pool: asyncpg connection pool (shared with the repository).
            cache_ttl_seconds: TTL of the in-process baseline cache; defaults to the
                BASELINE_CACHE_TTL env var (default 5.0).
            query_timeout_seconds: Per-query timeout so a slow DB fails fast to the neutral
                value on the inference hot path; defaults to BASELINE_QUERY_TIMEOUT (default 1.0).
        """
        self.pool = pool
        self.cache_ttl_seconds = cache_ttl_seconds if cache_ttl_seconds is not None else float(
            os.getenv("BASELINE_CACHE_TTL", "5.0")
        )
        self.query_timeout_seconds = query_timeout_seconds if query_timeout_seconds is not None else float(
            os.getenv("BASELINE_QUERY_TIMEOUT", "1.0")
        )
        self._cache = {}  # (schema, equipment_id, sensor_name, granularity) -> (expires_at, value)

    async def compute_rolling_mean(
        self,
        company_id: str,
        equipment_id: str,
        sensor_name: str,
        granularity: Optional[str] = None,
    ) -> Optional[float]:
        """Return the latest materialized mean (``avg_value``) for a sensor, or ``None``.

        ``None`` means "no baseline available" (no aggregate row yet, unknown granularity, or a
        read error) — the caller degrades to its neutral value. Errors are swallowed to ``None``
        rather than raised, so a degraded DB does not fail inference.
        """
        granularity = granularity or DEFAULT_GRANULARITY
        table = _AGG_TABLES.get(granularity)
        if table is None:
            logger.warning("Unknown aggregate granularity '%s'; no baseline", granularity)
            return None

        try:
            schema = _schema_for(company_id)
        except (ValueError, AttributeError, TypeError):
            logger.warning("Invalid company_id for baseline lookup: %r", company_id)
            return None

        key = (schema, str(equipment_id), sensor_name, granularity)
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached is not None and cached[0] > now:
            return cached[1]

        value = await self._read(schema, table, equipment_id, sensor_name)
        self._cache[key] = (now + self.cache_ttl_seconds, value)
        return value

    async def _read(self, schema, table, equipment_id, sensor_name) -> Optional[float]:
        # The aggregate is keyed by sensor_id (UUID); resolve it from the tenant's sensors table
        # by (equipment_id, sensor_name), then take the most recent closed window's mean.
        sql = (
            f"SELECT a.avg_value "
            f"FROM {table} a "
            f"JOIN sensors s ON s.sensor_id = a.sensor_id "
            f"WHERE s.equipment_id = $1::uuid AND s.sensor_name = $2 "
            f"ORDER BY a.time DESC LIMIT 1"
        )
        try:
            async with self.pool.acquire() as conn:
                # Tenant isolation via search_path (ADR-0003); schema is UUID-validated above.
                await conn.execute(f"SET search_path TO {schema}, public")
                row = await conn.fetchrow(sql, str(equipment_id), sensor_name,
                                          timeout=self.query_timeout_seconds)
        except Exception as e:  # noqa: BLE001 — degrade to neutral, never fail inference
            logger.warning("Baseline query failed for %s/%s: %s", equipment_id, sensor_name, e)
            return None
        if row is None or row["avg_value"] is None:
            return None
        return float(row["avg_value"])
