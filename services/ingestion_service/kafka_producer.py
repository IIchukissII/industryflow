# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Async Kafka producer for sensor data ingestion using aiokafka.
"""
import asyncio
import json
import logging
from typing import Optional
from aiokafka import AIOKafkaProducer
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

class AsyncKafkaProducerSingleton:
    """Singleton async Kafka producer for Ingestion Service"""
    _instance: Optional[AIOKafkaProducer] = None
    _started: bool = False

    @classmethod
    async def get_producer(cls) -> AIOKafkaProducer:
        """Get or create async Kafka producer instance"""
        if cls._instance is None:
            logger.info(f"Creating Kafka producer: {settings.kafka_bootstrap_servers}")
            
            cls._instance = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8'),
                acks="all",
                compression_type='snappy',
                linger_ms=10,
            )
            logger.info("Kafka producer created")

        if not cls._started:
            # Kafka may not be ready yet at startup (a recreate, or unordered pod start in k8s
            # where there is no depends_on). Retry the bootstrap with backoff instead of failing
            # startup hard — a single failed start() previously left the service stuck not
            # serving (edge 502) until a manual restart.
            attempts = settings.kafka_start_max_attempts if hasattr(settings, "kafka_start_max_attempts") else 30
            delay = 2.0
            last_err: Optional[Exception] = None
            for attempt in range(1, attempts + 1):
                try:
                    logger.info(f"Starting Kafka producer (attempt {attempt}/{attempts})...")
                    await cls._instance.start()
                    cls._started = True
                    logger.info("Kafka producer started successfully")
                    break
                except Exception as e:  # KafkaConnectionError and friends — Kafka not ready yet
                    last_err = e
                    logger.warning(f"Kafka not ready ({e}); retrying in {delay:.0f}s")
                    try:
                        await cls._instance.stop()
                    except Exception:
                        pass
                    cls._instance = AIOKafkaProducer(
                        bootstrap_servers=settings.kafka_bootstrap_servers,
                        value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8'),
                        acks="all",
                        compression_type='snappy',
                        linger_ms=10,
                    )
                    await asyncio.sleep(delay)
            else:
                raise RuntimeError(f"Kafka unavailable after {attempts} attempts: {last_err}")

        return cls._instance

    @classmethod
    async def close(cls):
        """Close producer and flush messages"""
        if cls._instance and cls._started:
            await cls._instance.stop()
            cls._instance = None
            cls._started = False
            logger.info("Kafka producer closed")

async def send_sensor_data(
    sensor_data: dict,
    topic: str = None
) -> bool:
    """
    Send sensor data to Kafka topic using async producer.

    Args:
        sensor_data: Dictionary with sensor data
        topic: Kafka topic name (defaults to settings)

    Returns:
        True if message sent successfully, False otherwise
    """
    if topic is None:
        topic = settings.KAFKA_TOPIC_SENSOR_DATA
    
    try:
        producer = await AsyncKafkaProducerSingleton.get_producer()
        
        record_metadata = await producer.send_and_wait(
            topic=topic,
            value=sensor_data,
            key=str(sensor_data['sensor_id']).encode('utf-8')
        )
        
        logger.debug(
            f"Message sent: topic={record_metadata.topic}, "
            f"partition={record_metadata.partition}, "
            f"offset={record_metadata.offset}"
        )
        
        return True

    except Exception as e:
        logger.error(f"Failed to send message to Kafka: {e}")
        return False

async def flush_producer():
    """Flush any pending messages"""
    producer = await AsyncKafkaProducerSingleton.get_producer()
    await producer.flush()
