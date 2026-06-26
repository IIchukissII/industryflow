# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Feature Store - Storage for Recent Sensor Readings

Stores time-series sensor data to enable:
- Windowed aggregations (rolling mean, std, etc.)
- Historical feature engineering
- Lag features and deltas

Backend: Redis (production) or In-Memory (development/testing)
"""
import json
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import redis.asyncio as redis

logger = logging.getLogger(__name__)


class FeatureStore:
    """
    Feature Store for storing and retrieving recent sensor readings

    Supports two backends:
    - Redis (production): Distributed, persistent, with TTL
    - In-Memory (development): Simple dict-based storage

    Data structure:
        Key: "features:{equipment_id}:{sensor_name}"
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

    async def close(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.aclose()

    def _make_key(self, equipment_id: str, sensor_name: str) -> str:
        """
        Generate Redis key for sensor readings

        Args:
            equipment_id: Equipment UUID
            sensor_name: Sensor name (e.g., "xmeas_7", "temperature")

        Returns:
            Redis key string
        """
        return f"features:{equipment_id}:{sensor_name}"

    async def store_reading(
        self,
        equipment_id: str,
        sensor_name: str,
        timestamp: str,
        value: float
    ) -> None:
        """
        Store a single sensor reading

        Args:
            equipment_id: Equipment UUID
            sensor_name: Sensor name
            timestamp: ISO 8601 timestamp
            value: Sensor value

        Returns:
            None
        """
        key = self._make_key(equipment_id, sensor_name)
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
        equipment_id: str,
        sensor_name: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """
        Get recent readings for a sensor

        Args:
            equipment_id: Equipment UUID
            sensor_name: Sensor name
            limit: Maximum number of readings to return (most recent)

        Returns:
            List of readings: [{timestamp, value}, ...]
            Ordered oldest to newest
        """
        key = self._make_key(equipment_id, sensor_name)

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
        equipment_id: str,
        sensor_names: List[str]
    ) -> Dict[str, float]:
        """
        Get current (most recent) values for multiple sensors

        Args:
            equipment_id: Equipment UUID
            sensor_names: List of sensor names

        Returns:
            Dictionary mapping sensor_name -> latest value
            Missing sensors will not be in the dict
        """
        snapshot = {}

        for sensor_name in sensor_names:
            readings = await self.get_recent_readings(
                equipment_id, sensor_name, limit=1
            )

            if readings:
                snapshot[sensor_name] = readings[-1]['value']

        logger.debug(f"Retrieved snapshot for {equipment_id}: {len(snapshot)}/{len(sensor_names)} sensors")
        return snapshot

    async def get_multiple_snapshots(
        self,
        equipment_id: str,
        sensor_names: List[str],
        count: int = 10
    ) -> List[Dict[str, float]]:
        """
        Get last N snapshots (aligned by timestamp)

        Args:
            equipment_id: Equipment UUID
            sensor_names: List of sensor names
            count: Number of historical snapshots to return

        Returns:
            List of snapshots, each is {sensor_name: value}
            Most recent snapshot is last in list
        """
        # Get readings for all sensors
        all_readings = {}
        for sensor_name in sensor_names:
            readings = await self.get_recent_readings(
                equipment_id, sensor_name, limit=count
            )
            all_readings[sensor_name] = readings

        # Align by index (assuming sensors are synchronized)
        # In production, you might align by timestamp instead
        snapshots = []
        max_length = max(len(readings) for readings in all_readings.values()) if all_readings else 0

        for i in range(max(0, max_length - count), max_length):
            snapshot = {}
            for sensor_name, readings in all_readings.items():
                if i < len(readings):
                    snapshot[sensor_name] = readings[i]['value']
            if snapshot:
                snapshots.append(snapshot)

        return snapshots

    async def clear_equipment(self, equipment_id: str) -> int:
        """
        Clear all sensor readings for an equipment

        Args:
            equipment_id: Equipment UUID

        Returns:
            Number of keys deleted
        """
        if self.use_memory:
            # In-memory: delete all keys matching pattern
            pattern = f"features:{equipment_id}:*"
            keys_to_delete = [k for k in self.storage.keys() if k.startswith(f"features:{equipment_id}:")]
            for key in keys_to_delete:
                del self.storage[key]
            count = len(keys_to_delete)
        else:
            # Redis: scan and delete
            pattern = f"features:{equipment_id}:*"
            cursor = 0
            count = 0

            while True:
                cursor, keys = await self.redis_client.scan(
                    cursor=cursor,
                    match=pattern,
                    count=100
                )
                if keys:
                    await self.redis_client.delete(*keys)
                    count += len(keys)

                if cursor == 0:
                    break

        logger.info(f"Cleared {count} sensor keys for equipment {equipment_id}")
        return count

    async def compute_rolling_mean(
        self,
        equipment_id: str,
        sensor_name: str,
        window: int = 50
    ) -> Optional[float]:
        """
        Compute rolling mean for a sensor over recent window

        Args:
            equipment_id: Equipment UUID
            sensor_name: Sensor name
            window: Number of recent readings to average

        Returns:
            Mean value, or None if insufficient data
        """
        readings = await self.get_recent_readings(equipment_id, sensor_name, limit=window)

        if not readings:
            return None

        values = [r['value'] for r in readings]
        return sum(values) / len(values) if values else None

    async def compute_rolling_std(
        self,
        equipment_id: str,
        sensor_name: str,
        window: int = 50
    ) -> Optional[float]:
        """
        Compute rolling standard deviation for a sensor

        Args:
            equipment_id: Equipment UUID
            sensor_name: Sensor name
            window: Number of recent readings

        Returns:
            Standard deviation, or None if insufficient data
        """
        readings = await self.get_recent_readings(equipment_id, sensor_name, limit=window)

        if len(readings) < 2:
            return None

        values = [r['value'] for r in readings]
        mean = sum(values) / len(values)
        variance = sum((x - mean) ** 2 for x in values) / len(values)
        return variance ** 0.5

    async def compute_statistics(
        self,
        equipment_id: str,
        sensor_name: str,
        window: int = 50
    ) -> Dict[str, Optional[float]]:
        """
        Compute multiple statistics for a sensor

        Args:
            equipment_id: Equipment UUID
            sensor_name: Sensor name
            window: Number of recent readings

        Returns:
            Dictionary with mean, std, min, max, current
        """
        readings = await self.get_recent_readings(equipment_id, sensor_name, limit=window)

        if not readings:
            return {
                'mean': None,
                'std': None,
                'min': None,
                'max': None,
                'current': None,
                'count': 0
            }

        values = [r['value'] for r in readings]
        mean_val = sum(values) / len(values)

        if len(values) >= 2:
            variance = sum((x - mean_val) ** 2 for x in values) / len(values)
            std_val = variance ** 0.5
        else:
            std_val = None

        return {
            'mean': mean_val,
            'std': std_val,
            'min': min(values),
            'max': max(values),
            'current': values[-1],
            'count': len(values)
        }

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


# Singleton instance (will be initialized in main.py)
feature_store: Optional[FeatureStore] = None


def get_feature_store() -> FeatureStore:
    """
    Get global Feature Store instance

    Returns:
        FeatureStore instance

    Raises:
        RuntimeError: If Feature Store not initialized
    """
    if feature_store is None:
        raise RuntimeError("Feature Store not initialized. Call init_feature_store() first.")
    return feature_store


async def init_feature_store(
    redis_url: Optional[str] = None,
    max_window: int = 100,
    ttl_seconds: int = 3600,
    use_memory: bool = False
) -> FeatureStore:
    """
    Initialize global Feature Store instance

    Args:
        redis_url: Redis connection URL
        max_window: Maximum readings to store per sensor
        ttl_seconds: TTL for Redis keys
        use_memory: Use in-memory storage instead of Redis

    Returns:
        Initialized FeatureStore instance
    """
    global feature_store
    feature_store = FeatureStore(
        redis_url=redis_url,
        max_window=max_window,
        ttl_seconds=ttl_seconds,
        use_memory=use_memory
    )
    logger.info("Feature Store initialized successfully")
    return feature_store
