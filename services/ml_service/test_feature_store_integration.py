#!/usr/bin/env python3
"""
Test Feature Store integration with Redis
Simulates sensor data storage and retrieval
"""
import asyncio
import sys
import os
from datetime import datetime, timezone

# Add API directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'api'))

from feature_engineering.feature_store import FeatureStore


async def test_feature_store():
    """Test Feature Store with Redis backend"""

    print("=" * 60)
    print("Feature Store Integration Test")
    print("=" * 60)

    # Initialize Feature Store with Redis
    redis_url = "redis://localhost:6379/0"
    print(f"\n1. Connecting to Redis: {redis_url}")

    feature_store = FeatureStore(
        redis_url=redis_url,
        use_memory=False,
        max_window=100,
        ttl_seconds=3600
    )

    # Test health check
    print("\n2. Testing health check...")
    health = await feature_store.health_check()
    print(f"   Backend: {health['backend']}")
    print(f"   Status: {health['status']}")
    if 'redis_keys' in health:
        print(f"   Redis keys: {health['redis_keys']}")
    elif 'total_keys' in health:
        print(f"   Total keys: {health['total_keys']}")

    # Simulate TEP sensor data
    equipment_id = "550e8400-e29b-41d4-a716-446655440100"
    sensor_names = ["xmeas_7", "xmeas_8", "xmeas_18"]

    print(f"\n3. Storing sensor readings for equipment {equipment_id[:8]}...")

    # Store readings for multiple sensors
    for i, sensor_name in enumerate(sensor_names):
        timestamp = datetime.now(timezone.utc).isoformat()
        value = 100.0 + i * 10.0

        await feature_store.store_reading(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            timestamp=timestamp,
            value=value
        )
        print(f"   ✓ Stored {sensor_name}: {value}")

    # Retrieve readings
    print(f"\n4. Retrieving readings...")
    for sensor_name in sensor_names:
        readings = await feature_store.get_recent_readings(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            limit=5
        )
        print(f"   {sensor_name}: {len(readings)} readings")
        if readings:
            print(f"     Latest: {readings[-1]}")

    # Get current snapshot
    print(f"\n5. Getting current snapshot...")
    snapshot = await feature_store.get_current_snapshot(
        equipment_id=equipment_id,
        sensor_names=sensor_names
    )
    print(f"   Snapshot: {snapshot}")

    # Check total keys again
    print(f"\n6. Final health check...")
    health = await feature_store.health_check()
    total_keys = health.get('redis_keys', health.get('total_keys', 0))
    print(f"   Total keys: {total_keys}")

    # List some keys
    if total_keys > 0:
        print(f"\n7. Sample keys in Redis:")
        # Use Redis client to list keys
        import redis.asyncio as redis
        redis_client = redis.from_url(redis_url, decode_responses=True)
        keys = await redis_client.keys("features:*")
        for key in keys[:10]:
            print(f"   - {key}")
        await redis_client.aclose()

    # Cleanup
    await feature_store.close()

    print("\n" + "=" * 60)
    print("✓ Feature Store integration test completed successfully!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_feature_store())
