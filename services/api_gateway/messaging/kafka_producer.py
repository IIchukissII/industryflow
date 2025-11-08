"""
Async Kafka producer for sensor data ingestion using aiokafka.
"""
import json
import logging
from typing import Optional
from aiokafka import AIOKafkaProducer
from config import get_settings

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
settings = get_settings()


class AsyncKafkaProducerSingleton:
    """Singleton async Kafka producer for the API service"""
    _instance: Optional[AIOKafkaProducer] = None
    _started: bool = False

    @classmethod
    async def get_producer(cls) -> AIOKafkaProducer:
        """Get or create async Kafka producer instance"""
        logger.info("🔵 get_producer() called")
        logger.info(f"🔵 _instance is None: {cls._instance is None}")
        logger.info(f"🔵 _started: {cls._started}")

        if cls._instance is None:
            logger.info("🔵 Creating new producer instance...")
            logger.info(f"🔵 Bootstrap servers: {settings.kafka_bootstrap_servers}")

            cls._instance = AIOKafkaProducer(
                bootstrap_servers=settings.kafka_bootstrap_servers,
                value_serializer=lambda v: json.dumps(v, default=str).encode('utf-8'),
                acks='all',
                compression_type='snappy',
                linger_ms=10,
            )
            logger.info("✅ Async Kafka producer created")

        if not cls._started:
            logger.info("🔵 Starting producer...")
            try:
                await cls._instance.start()
                cls._started = True
                logger.info("✅ Async Kafka producer started")
                logger.info(f"🔵 Producer client: {cls._instance.client}")
            except Exception as e:
                logger.error(f"❌ Failed to start producer: {e}")
                logger.exception("Full traceback:")
                raise

        logger.info("🔵 Returning producer instance")
        return cls._instance

    @classmethod
    async def close(cls):
        """Close producer and flush messages"""
        if cls._instance and cls._started:
            await cls._instance.stop()
            cls._instance = None
            cls._started = False
            logger.info("🔒 Async Kafka producer closed")


async def send_sensor_data(
    sensor_data: dict,
    topic: str = "sensor-data-raw"
) -> bool:
    """
    Send sensor data to Kafka topic using async producer.

    Args:
        sensor_data: Dictionary with sensor data
        topic: Kafka topic name

    Returns:
        True if message sent successfully, False otherwise
    """
    # Print statements to bypass logging config
    print("=" * 80, flush=True)
    print(f"🔵 SEND_SENSOR_DATA CALLED! sensor_id: {sensor_data.get('sensor_id')}", flush=True)
    print(f"🔵 Topic: {topic}", flush=True)
    print("=" * 80, flush=True)

    try:
        logger.info("=" * 80)
        logger.info(f"🔵 SEND START - sensor_id: {sensor_data.get('sensor_id')}")
        logger.info(f"🔵 Topic: {topic}")
        logger.info(f"🔵 Value: {sensor_data.get('value')}")

        logger.info("🔵 Getting producer...")
        producer = await AsyncKafkaProducerSingleton.get_producer()
        logger.info(f"🔵 Got producer: {producer}")
        logger.info(f"🔵 Producer client: {producer.client}")

        # Try to send
        logger.info("🔵 Calling send_and_wait()...")
        record_metadata = await producer.send_and_wait(
            topic=topic,
            value=sensor_data,
            key=str(sensor_data['sensor_id']).encode('utf-8')
        )

        print("✅ SEND SUCCESS!", flush=True)
        print(f"✅ Topic: {record_metadata.topic}", flush=True)
        print(f"✅ Partition: {record_metadata.partition}", flush=True)
        print(f"✅ Offset: {record_metadata.offset}", flush=True)
        print("=" * 80, flush=True)

        logger.info("✅ SEND SUCCESS!")
        logger.info(f"✅ Topic: {record_metadata.topic}")
        logger.info(f"✅ Partition: {record_metadata.partition}")
        logger.info(f"✅ Offset: {record_metadata.offset}")
        logger.info(f"✅ Timestamp: {record_metadata.timestamp}")
        logger.info("=" * 80)

        return True

    except Exception as e:
        print("=" * 80, flush=True)
        print(f"❌ SEND FAILED: {e}", flush=True)
        print(f"❌ Type: {type(e)}", flush=True)
        print("=" * 80, flush=True)

        logger.error("=" * 80)
        logger.error(f"❌ SEND FAILED: {e}")
        logger.error(f"❌ Type: {type(e)}")
        logger.error(f"❌ Args: {e.args}")
        logger.exception("Full traceback:")
        logger.error("=" * 80)
        return False


async def flush_producer():
    """Flush any pending messages"""
    producer = await AsyncKafkaProducerSingleton.get_producer()
    await producer.flush()