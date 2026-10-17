# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Latest-value snapshot store for the alert worker's ML rules.

Holds each sensor's recent readings per tenant and equipment; the ML rules read the latest value
of each sensor as one snapshot (ADR-0023 dec 7). No windowed or derived features are computed here.

Backend: Redis (production) or In-Memory (development/testing)
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Tuple
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class FeatureStore:
    """
    Feature Store for storing and retrieving recent sensor readings

    Supports two backends:
    - Redis (production): Distributed, persistent, with TTL
    - In-Memory (development): Simple dict-based storage

    Data structure:
        Key: "features:{company_id}:{equipment_id}:{sensor_name}"
             company_id is the tenant the reading was stamped with at ingestion (from the device
             certificate), so one tenant's key can never name another tenant's readings, whatever
             the equipment ids are.
        Value: JSON array of [{timestamp, value}, ...]
        Window: Last N readings (default: 100)
        TTL: 3600 seconds (1 hour)
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        max_window: int = 100,
        ttl_seconds: int = 3600,
        use_memory: bool = False
    ):
        """
        Initialize Feature Store

        Args:
            redis_url: Redis connection URL (e.g., "redis://localhost:6379/0")
            max_window: Maximum number of readings to store per sensor
            ttl_seconds: Time-to-live for keys in seconds
            use_memory: If True, use in-memory dict instead of Redis (for testing)
        """
        self.max_window = max_window
        self.ttl_seconds = ttl_seconds
        self.use_memory = use_memory

        if use_memory:
            logger.info("Using in-memory Feature Store (development mode)")
            self.storage = {}  # {key: [readings]}
            self.redis_client = None
        else:
            logger.info(f"Connecting to Redis Feature Store: {redis_url}")
            self.redis_client = redis.from_url(
                redis_url or "redis://localhost:6379/0",
                encoding="utf-8",
                decode_responses=True
            )
            self.storage = None

    async def ping(self) -> None:
        """Raise unless the backend answers. Called at startup so a wrong or unreachable
        REDIS_URL stops the worker instead of leaving the ML rules silently blind."""
        if self.redis_client:
            await self.redis_client.ping()

    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.aclose()

    @staticmethod
    def _make_key(company_id: str, equipment_id: str, sensor_name: str) -> str:
        """
        Generate Redis key for sensor readings

        Args:
            company_id: Tenant UUID (canonicalised, so one tenant has exactly one key space)
            equipment_id: Equipment UUID
            sensor_name: Sensor name (e.g., "pressure_1", "temperature")

        Returns:
            Redis key string
        """
        tenant = str(uuid.UUID(str(company_id)))
        equipment = str(uuid.UUID(str(equipment_id)))
        return f"features:{tenant}:{equipment}:{sensor_name}"

    async def store_reading(
        self,
        company_id: str,
        equipment_id: str,
        sensor_name: str,
        timestamp: str,
        value: float
    ) -> None:
        """
        Store a single sensor reading

        Args:
            company_id: Tenant UUID
            equipment_id: Equipment UUID
            sensor_name: Sensor name
            timestamp: ISO 8601 timestamp
            value: Sensor value

        Returns:
            None
        """
        key = self._make_key(company_id, equipment_id, sensor_name)
        reading = {
            "timestamp": timestamp,
            "value": float(value)
        }

        if self.use_memory:
            # In-memory storage
            if key not in self.storage:
                self.storage[key] = []

            self.storage[key].append(reading)

            # Trim to max window
            if len(self.storage[key]) > self.max_window:
                self.storage[key] = self.storage[key][-self.max_window:]

        else:
            # Redis storage
            # Get existing readings
            existing = await self.redis_client.get(key)
            if existing:
                readings = json.loads(existing)
            else:
                readings = []

            # Append new reading
            readings.append(reading)

            # Trim to max window
            if len(readings) > self.max_window:
                readings = readings[-self.max_window:]

            # Store back with TTL
            await self.redis_client.setex(
                key,
                self.ttl_seconds,
                json.dumps(readings)
            )

        logger.debug(f"Stored reading: {sensor_name}={value} at {timestamp}")

    async def get_recent_readings(
        self,
        company_id: str,
        equipment_id: str,
        sensor_name: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get recent readings for a sensor

        Args:
            company_id: Tenant UUID
            equipment_id: Equipment UUID
            sensor_name: Sensor name
            limit: Maximum number of readings to return (most recent)

        Returns:
            List of readings: [{timestamp, value}, ...]
            Ordered oldest to newest
        """
        key = self._make_key(company_id, equipment_id, sensor_name)

        if self.use_memory:
            # In-memory storage
            readings = self.storage.get(key, [])
        else:
            # Redis storage
            data = await self.redis_client.get(key)
            if data:
                readings = json.loads(data)
            else:
                readings = []

        # Return last N readings
        return readings[-limit:] if readings else []

    async def get_current_snapshot(
        self,
        company_id: str,
        equipment_id: str,
        sensor_names: List[str],
        max_age_seconds: Optional[float] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[Dict[str, float], Optional[datetime]]:
        """
        Get current (most recent) values for multiple sensors

        Args:
            company_id: Tenant UUID
            equipment_id: Equipment UUID
            sensor_names: List of sensor names
            max_age_seconds: A sensor whose latest reading is older than this (by its own event
                time) is left out, exactly as if it had never reported: a stale value is not a
                current one. None disables the bound.
            now: Reference time for the age check (defaults to the current UTC time)

        Returns:
            (snapshot, as_of): snapshot maps sensor_name -> latest value, missing and stale
            sensors are not in it; as_of is the newest event time among the values used (None
            when the snapshot is empty).
        """
        now = now or datetime.now(timezone.utc)
        snapshot: Dict[str, float] = {}
        as_of: Optional[datetime] = None
        stale = []

        for sensor_name in sensor_names:
            readings = await self.get_recent_readings(
                company_id, equipment_id, sensor_name, limit=1
            )
            if not readings:
                continue

            latest = readings[-1]
            at = reading_time(latest.get('timestamp'))
            if max_age_seconds is not None and (at is None or (now - at).total_seconds() > max_age_seconds):
                stale.append(sensor_name)
                continue

            snapshot[sensor_name] = latest['value']
            if at is not None and (as_of is None or at > as_of):
                as_of = at

        if stale:
            logger.info(f"Stale readings left out of the snapshot for {equipment_id}: {stale}")
        logger.debug(f"Retrieved snapshot for {equipment_id}: {len(snapshot)}/{len(sensor_names)} sensors")
        return snapshot, as_of

    async def health_check(self) -> Dict[str, Any]:
        """
        Check Feature Store health

        Returns:
            Health status dict
        """
        if self.use_memory:
            return {
                "backend": "memory",
                "status": "healthy",
                "total_keys": len(self.storage),
                "config": {
                    "max_window": self.max_window,
                    "ttl_seconds": self.ttl_seconds
                }
            }
        else:
            try:
                # Ping Redis
                await self.redis_client.ping()

                # Get info
                info = await self.redis_client.info('stats')

                return {
                    "backend": "redis",
                    "status": "healthy",
                    "redis_keys": info.get('db0', {}).get('keys', 0),
                    "config": {
                        "max_window": self.max_window,
                        "ttl_seconds": self.ttl_seconds
                    }
                }
            except Exception as e:
                logger.error(f"Feature Store health check failed: {e}")
                return {
                    "backend": "redis",
                    "status": "unhealthy",
                    "error": str(e)
                }


def reading_time(ts: Any) -> Optional[datetime]:
    """A stored reading's event time as aware UTC, or None when it is absent or unparseable."""
    if not isinstance(ts, str):
        return None
    try:
        at = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    except ValueError:
        return None
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
