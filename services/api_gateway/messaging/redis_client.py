# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Redis client for caching sensor data.

A latest reading is keyed by its tenant and its sensor: a sensor id is unique only within a tenant's
schema, so another tenant may choose the same one.
"""
import json
from typing import Dict, Any
import redis.asyncio as aioredis
from industryflow_contracts import tenancy

from config import get_settings

settings = get_settings()


def latest_key(company_id: str, sensor_id: str) -> str:
    return tenancy.latest_reading_prefix(company_id) + str(sensor_id)


class RedisClient:
    def __init__(self):
        self.redis = None
    
    async def connect(self):
        """Connect to Redis"""
        redis_url = settings.redis_url
        self.redis = await aioredis.from_url(
            redis_url,
            encoding="utf-8",
            decode_responses=True
        )
        # Test connection
        await self.redis.ping()
        print("✓ Redis connected")
    
    async def disconnect(self):
        """Disconnect from Redis."""
        if self.redis:
            await self.redis.close()
            print("✓ Redis disconnected")
    
    async def set_sensor_value(
            self,
            company_id: str,
            sensor_id: str,
            value: float,
            metadata: Dict[str, Any],
            ttl: int = 3600
    ):
        """Cache a tenant's latest sensor value with its metadata."""
        key = latest_key(company_id, sensor_id)
        data = {
            "value": value,
            "timestamp": metadata.get("timestamp"),
            "equipment_id": metadata.get("equipment_id"),
            "company_id": tenancy.canonical_company_id(company_id),
            "site_id": metadata.get("site_id"),
            "unit": metadata.get("unit"),
            "quality_code": metadata.get("quality_code")
        }
        await self.redis.setex(
            key,
            ttl,
            json.dumps(data)
        )
    
    async def get_all_sensors(self, company_id: str) -> Dict[str, Dict[str, Any]]:
        """The tenant's cached sensor values, by sensor id."""
        prefix = tenancy.latest_reading_prefix(company_id)
        result = {}
        async for key in self.redis.scan_iter(match=f"{prefix}*", count=500):
            sensor_id = tenancy.owned_name(prefix, key)
            data = await self.redis.get(key) if sensor_id else None
            if data:
                result[sensor_id] = json.loads(data)
        return result

# Global Redis client instance
redis_client = RedisClient()
