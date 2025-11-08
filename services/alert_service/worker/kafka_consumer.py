"""
Async Kafka Consumer for Alert Detection
Schema-per-tenant architecture
"""
import asyncio
import json
import logging
from typing import Optional
from datetime import datetime
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

logger = logging.getLogger(__name__)


class AlertKafkaConsumer:
    """Kafka consumer for sensor data with schema-per-tenant routing"""
    
    def __init__(self, config, rules_engine):
        self.config = config
        self.rules_engine = rules_engine
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.producer: Optional[AIOKafkaProducer] = None
        self.running = False
    
    async def start(self):
        """Initialize Kafka consumer and producer"""
        logger.info("Starting Kafka consumer...")
        
        # Create consumer
        self.consumer = AIOKafkaConsumer(
            self.config.KAFKA_TOPIC_SENSOR_DATA,
            bootstrap_servers=self.config.KAFKA_BOOTSTRAP_SERVERS,
            group_id=self.config.KAFKA_GROUP_ID,
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            auto_offset_reset='latest',
            enable_auto_commit=True
        )
        
        # Create producer for alerts
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.config.KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode('utf-8')
        )
        
        await self.consumer.start()
        await self.producer.start()
        
        logger.info(f"Connected to Kafka: {self.config.KAFKA_BOOTSTRAP_SERVERS}")
        logger.info(f"Consuming from: {self.config.KAFKA_TOPIC_SENSOR_DATA}")
        logger.info(f"Publishing alerts to: {self.config.KAFKA_TOPIC_ALERTS}")
        
        self.running = True
    
    async def consume_messages(self):
        """Main message consumption loop"""
        logger.info("Starting message consumption...")
        
        try:
            while self.running:
                # Get batch of messages
                data = await self.consumer.getmany(timeout_ms=1000, max_records=10)
                
                if data:
                    for tp, messages in data.items():
                        for message in messages:
                            try:
                                sensor_data = message.value
                                await self._process_message(sensor_data)
                            except Exception as e:
                                logger.error(f"Error processing message: {e}", exc_info=True)
                else:
                    await asyncio.sleep(0.1)
        
        except asyncio.CancelledError:
            logger.info("Message consumption cancelled")
        except Exception as e:
            logger.error(f"Fatal error in consumer loop: {e}", exc_info=True)
    
    async def _process_message(self, sensor_data: dict):
        """Process a single sensor message"""
        # Extract company_id for schema routing
        company_id = sensor_data.get('company_id')
        
        if not company_id:
            logger.warning("Message missing company_id, skipping")
            return
        
        logger.debug(f"Processing sensor={sensor_data.get('sensor_id')}, company={company_id}")
        
        # Evaluate rules (schema routing happens in rules_engine)
        triggered_alerts = await self.rules_engine.evaluate(sensor_data, company_id)
        
        if triggered_alerts:
            logger.info(f"Triggered {len(triggered_alerts)} alert(s) for company {company_id}")
            
            for alert in triggered_alerts:
                await self._publish_alert(alert)
    
    async def _publish_alert(self, alert: dict):
        """Publish alert to Kafka"""
        try:
            await self.producer.send_and_wait(
                self.config.KAFKA_TOPIC_ALERTS,
                value=alert
            )
            logger.debug(f"Published alert: {alert['alert_id']}")
        except Exception as e:
            logger.error(f"Failed to publish alert: {e}", exc_info=True)
    
    async def stop(self):
        """Stop Kafka consumer and producer"""
        logger.info("Stopping Kafka consumer...")
        self.running = False
        
        if self.consumer:
            await self.consumer.stop()
        if self.producer:
            await self.producer.stop()
        
        logger.info("Kafka consumer stopped")
