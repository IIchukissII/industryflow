"""
Async Kafka producer for sensor data ingestion using aiokafka.
"""
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
            logger.info("Starting Kafka producer...")
            await cls._instance.start()
            cls._started = True
            logger.info("Kafka producer started successfully")

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
        topic = settings.KAFKA_TOPIC_RAW
    
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
