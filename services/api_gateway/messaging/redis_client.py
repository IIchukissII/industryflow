"""Redis client for caching sensor data."""
import json
import os
from typing import Optional, Dict, Any
import redis.asyncio as aioredis

from config import get_settings

settings = get_settings()

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
            sensor_id: str,
            value: float,
            metadata: Dict[str, Any],
            ttl: int = 3600
    ):
        """Cache latest sensor value with metadata including company_id."""
        key = f"sensor:latest:{sensor_id}"
        data = {
            "value": value,
            "timestamp": metadata.get("timestamp"),
            "equipment_id": metadata.get("equipment_id"),
            "company_id": metadata.get("company_id"),  # Added company_id
            "site_id": metadata.get("site_id"),
            "unit": metadata.get("unit"),
            "quality_code": metadata.get("quality_code")
        }
        await self.redis.setex(
            key,
            ttl,
            json.dumps(data)
        )
    
    async def get_sensor_value(self, sensor_id: str) -> Optional[Dict[str, Any]]:
        """Get cached sensor value."""
        key = f"sensor:latest:{sensor_id}"
        data = await self.redis.get(key)
        if data:
            return json.loads(data)
        return None
    
    async def get_all_sensors(self) -> Dict[str, Dict[str, Any]]:
        """Get all cached sensor values."""
        keys = await self.redis.keys("sensor:latest:*")
        result = {}
        
        for key in keys:
            sensor_id = key.replace("sensor:latest:", "")
            data = await self.redis.get(key)
            if data:
                result[sensor_id] = json.loads(data)
        
        return result

# Global Redis client instance
redis_client = RedisClient()
