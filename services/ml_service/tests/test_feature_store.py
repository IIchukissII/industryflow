# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Unit Tests for Feature Store
Tests both in-memory and Redis backends
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'api'))

from feature_engineering.feature_store import FeatureStore


class TestFeatureStoreInMemory:
    """Test Feature Store with in-memory backend"""

    @pytest_asyncio.fixture
    async def feature_store(self):
        """Create in-memory Feature Store for testing"""
        store = FeatureStore(
            use_memory=True,
            max_window=10,  # Small window for testing
            ttl_seconds=3600
        )
        yield store
        await store.close()

    @pytest.mark.asyncio
    async def test_store_single_reading(self, feature_store):
        """Test storing a single sensor reading"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        sensor_name = "xmeas_7"
        timestamp = datetime.now(timezone.utc).isoformat()
        value = 123.45

        await feature_store.store_reading(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            timestamp=timestamp,
            value=value
        )

        # Retrieve and verify
        readings = await feature_store.get_recent_readings(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            limit=1
        )

        assert len(readings) == 1
        assert readings[0]['value'] == value
        assert readings[0]['timestamp'] == timestamp

    @pytest.mark.asyncio
    async def test_store_multiple_readings(self, feature_store):
        """Test storing multiple readings for same sensor"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        sensor_name = "xmeas_18"

        # Store 5 readings
        for i in range(5):
            timestamp = (datetime.now(timezone.utc) + timedelta(seconds=i)).isoformat()
            await feature_store.store_reading(
                equipment_id=equipment_id,
                sensor_name=sensor_name,
                timestamp=timestamp,
                value=100.0 + i
            )

        # Retrieve all
        readings = await feature_store.get_recent_readings(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            limit=10
        )

        assert len(readings) == 5
        assert readings[0]['value'] == 100.0
        assert readings[4]['value'] == 104.0

    @pytest.mark.asyncio
    async def test_window_limit(self, feature_store):
        """Test that window limit is enforced (max_window=10)"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        sensor_name = "xmeas_1"

        # Store 15 readings (window is 10)
        for i in range(15):
            timestamp = (datetime.now(timezone.utc) + timedelta(seconds=i)).isoformat()
            await feature_store.store_reading(
                equipment_id=equipment_id,
                sensor_name=sensor_name,
                timestamp=timestamp,
                value=float(i)
            )

        # Should only have last 10
        readings = await feature_store.get_recent_readings(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            limit=20
        )

        assert len(readings) == 10
        assert readings[0]['value'] == 5.0  # First 5 dropped
        assert readings[9]['value'] == 14.0

    @pytest.mark.asyncio
    async def test_get_current_snapshot(self, feature_store):
        """Test getting current snapshot of multiple sensors"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        sensor_names = ["xmeas_7", "xmeas_8", "xmeas_9"]
        timestamp = datetime.now(timezone.utc).isoformat()

        # Store readings for 3 sensors
        for i, sensor_name in enumerate(sensor_names):
            await feature_store.store_reading(
                equipment_id=equipment_id,
                sensor_name=sensor_name,
                timestamp=timestamp,
                value=50.0 + i
            )

        # Get snapshot
        snapshot = await feature_store.get_current_snapshot(
            equipment_id=equipment_id,
            sensor_names=sensor_names
        )

        assert len(snapshot) == 3
        assert snapshot["xmeas_7"] == 50.0
        assert snapshot["xmeas_8"] == 51.0
        assert snapshot["xmeas_9"] == 52.0

    @pytest.mark.asyncio
    async def test_get_snapshot_missing_sensors(self, feature_store):
        """Test snapshot when some sensors have no data"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        timestamp = datetime.now(timezone.utc).isoformat()

        # Only store data for xmeas_7
        await feature_store.store_reading(
            equipment_id=equipment_id,
            sensor_name="xmeas_7",
            timestamp=timestamp,
            value=100.0
        )

        # Request snapshot for 3 sensors
        snapshot = await feature_store.get_current_snapshot(
            equipment_id=equipment_id,
            sensor_names=["xmeas_7", "xmeas_8", "xmeas_9"]
        )

        # Should only have xmeas_7
        assert len(snapshot) == 1
        assert "xmeas_7" in snapshot
        assert "xmeas_8" not in snapshot
        assert "xmeas_9" not in snapshot

    @pytest.mark.asyncio
    async def test_get_multiple_snapshots(self, feature_store):
        """Test getting multiple historical snapshots"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        sensor_names = ["xmeas_7", "xmeas_8"]

        # Store 5 readings for 2 sensors
        for i in range(5):
            timestamp = (datetime.now(timezone.utc) + timedelta(seconds=i)).isoformat()
            for sensor_name in sensor_names:
                await feature_store.store_reading(
                    equipment_id=equipment_id,
                    sensor_name=sensor_name,
                    timestamp=timestamp,
                    value=100.0 + i
                )

        # Get last 3 snapshots
        snapshots = await feature_store.get_multiple_snapshots(
            equipment_id=equipment_id,
            sensor_names=sensor_names,
            count=3
        )

        assert len(snapshots) == 3
        # Last snapshot should have values 104.0
        assert snapshots[2]["xmeas_7"] == 104.0
        assert snapshots[2]["xmeas_8"] == 104.0

    @pytest.mark.asyncio
    async def test_clear_equipment(self, feature_store):
        """Test clearing all sensor data for an equipment"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        timestamp = datetime.now(timezone.utc).isoformat()

        # Store data for 3 sensors
        for sensor_name in ["xmeas_7", "xmeas_8", "xmeas_9"]:
            await feature_store.store_reading(
                equipment_id=equipment_id,
                sensor_name=sensor_name,
                timestamp=timestamp,
                value=100.0
            )

        # Clear equipment
        count = await feature_store.clear_equipment(equipment_id)
        assert count == 3

        # Verify data is gone
        snapshot = await feature_store.get_current_snapshot(
            equipment_id=equipment_id,
            sensor_names=["xmeas_7", "xmeas_8", "xmeas_9"]
        )
        assert len(snapshot) == 0

    @pytest.mark.asyncio
    async def test_different_equipment_isolation(self, feature_store):
        """Test that different equipment IDs are isolated"""
        equipment_id_1 = "550e8400-e29b-41d4-a716-446655440100"
        equipment_id_2 = "550e8400-e29b-41d4-a716-446655440200"
        sensor_name = "xmeas_7"
        timestamp = datetime.now(timezone.utc).isoformat()

        # Store different values for same sensor on different equipment
        await feature_store.store_reading(
            equipment_id=equipment_id_1,
            sensor_name=sensor_name,
            timestamp=timestamp,
            value=100.0
        )

        await feature_store.store_reading(
            equipment_id=equipment_id_2,
            sensor_name=sensor_name,
            timestamp=timestamp,
            value=200.0
        )

        # Verify isolation
        readings_1 = await feature_store.get_recent_readings(
            equipment_id=equipment_id_1,
            sensor_name=sensor_name,
            limit=1
        )

        readings_2 = await feature_store.get_recent_readings(
            equipment_id=equipment_id_2,
            sensor_name=sensor_name,
            limit=1
        )

        assert readings_1[0]['value'] == 100.0
        assert readings_2[0]['value'] == 200.0

    @pytest.mark.asyncio
    async def test_health_check(self, feature_store):
        """Test health check endpoint"""
        health = await feature_store.health_check()

        assert health['backend'] == 'memory'
        assert health['status'] == 'healthy'
        assert 'total_keys' in health
        assert health['config']['max_window'] == 10
        assert health['config']['ttl_seconds'] == 3600

    @pytest.mark.asyncio
    async def test_empty_readings(self, feature_store):
        """Test getting readings when none exist"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        sensor_name = "xmeas_99"

        readings = await feature_store.get_recent_readings(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            limit=10
        )

        assert readings == []

    @pytest.mark.asyncio
    async def test_limit_parameter(self, feature_store):
        """Test that limit parameter works correctly"""
        equipment_id = "550e8400-e29b-41d4-a716-446655440100"
        sensor_name = "xmeas_7"

        # Store 10 readings
        for i in range(10):
            timestamp = (datetime.now(timezone.utc) + timedelta(seconds=i)).isoformat()
            await feature_store.store_reading(
                equipment_id=equipment_id,
                sensor_name=sensor_name,
                timestamp=timestamp,
                value=float(i)
            )

        # Request only 5
        readings = await feature_store.get_recent_readings(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            limit=5
        )

        assert len(readings) == 5
        # Should get last 5 (values 5-9)
        assert readings[0]['value'] == 5.0
        assert readings[4]['value'] == 9.0


class TestFeatureStoreMakeKey:
    """Test key generation"""

    def test_make_key_format(self):
        """Test Redis key format"""
        store = FeatureStore(use_memory=True)

        key = store._make_key(
            equipment_id="550e8400-e29b-41d4-a716-446655440100",
            sensor_name="xmeas_7"
        )

        assert key == "features:550e8400-e29b-41d4-a716-446655440100:xmeas_7"

    def test_make_key_different_sensors(self):
        """Test that different sensors get different keys"""
        store = FeatureStore(use_memory=True)

        key1 = store._make_key("equip1", "sensor1")
        key2 = store._make_key("equip1", "sensor2")

        assert key1 != key2
        assert "sensor1" in key1
        assert "sensor2" in key2


# Integration tests with Redis (skip if Redis not available)
@pytest.mark.redis
@pytest.mark.asyncio
class TestFeatureStoreRedis:
    """Test Feature Store with real Redis backend"""

    @pytest_asyncio.fixture
    async def feature_store(self):
        """Create Redis-backed Feature Store"""
        store = FeatureStore(
            redis_url="redis://localhost:6379/1",  # Use DB 1 for tests
            use_memory=False,
            max_window=10,
            ttl_seconds=60  # Short TTL for tests
        )
        yield store
        # Cleanup
        await store.clear_equipment("test-equipment-1")
        await store.clear_equipment("test-equipment-2")
        await store.close()

    async def test_redis_store_and_retrieve(self, feature_store):
        """Test basic store and retrieve with Redis"""
        equipment_id = "test-equipment-1"
        sensor_name = "test-sensor"
        timestamp = datetime.now(timezone.utc).isoformat()
        value = 42.0

        await feature_store.store_reading(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            timestamp=timestamp,
            value=value
        )

        readings = await feature_store.get_recent_readings(
            equipment_id=equipment_id,
            sensor_name=sensor_name,
            limit=1
        )

        assert len(readings) == 1
        assert readings[0]['value'] == value

    async def test_redis_health_check(self, feature_store):
        """Test Redis health check"""
        health = await feature_store.health_check()

        assert health['backend'] == 'redis'
        assert health['status'] == 'healthy'


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v", "-s"])
