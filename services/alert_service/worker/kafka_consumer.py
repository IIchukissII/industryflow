# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

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
        # At-least-once handling parameters (ADR-0005).
        self._max_retries = getattr(config, 'KAFKA_MAX_RETRIES', 3)
        self._retry_backoff = getattr(config, 'KAFKA_RETRY_BACKOFF_SECONDS', 1.0)
        self._dlq_topic = getattr(
            config, 'KAFKA_TOPIC_DLQ', f"{config.KAFKA_TOPIC_SENSOR_DATA}.dlq"
        )
    
    async def start(self):
        """Initialize Kafka consumer and producer"""
        logger.info("Starting Kafka consumer...")
        
        # Create consumer. At-least-once (ADR-0005): start from the earliest offset so a
        # fresh consumer does not skip the backlog, and commit manually only after a batch
        # is fully handled (enable_auto_commit=False).
        self.consumer = AIOKafkaConsumer(
            self.config.KAFKA_TOPIC_SENSOR_DATA,
            bootstrap_servers=self.config.KAFKA_BOOTSTRAP_SERVERS,
            group_id=self.config.KAFKA_GROUP_ID,
            value_deserializer=lambda m: json.loads(m.decode('utf-8')),
            auto_offset_reset='earliest',
            enable_auto_commit=False
        )

        # Create producer for alerts and the dead-letter topic. acks='all' + idempotence
        # so a message is durably replicated before it counts as sent (ADR-0005).
        self.producer = AIOKafkaProducer(
            bootstrap_servers=self.config.KAFKA_BOOTSTRAP_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode('utf-8'),
            acks='all',
            enable_idempotence=True
        )
        
        await self.consumer.start()
        await self.producer.start()
        
        logger.info(f"Connected to Kafka: {self.config.KAFKA_BOOTSTRAP_SERVERS}")
        logger.info(f"Consuming from: {self.config.KAFKA_TOPIC_SENSOR_DATA}")
        logger.info(f"Publishing alerts to: {self.config.KAFKA_TOPIC_ALERTS}")
        
        self.running = True
    
    async def consume_messages(self):
        """
        Main consumption loop with at-least-once semantics (ADR-0005).

        Each getmany batch is processed in full and only then committed. If any message
        cannot be handled (processed, or dead-lettered after retries), the batch is not
        committed and the consumer is rewound to the last committed offsets, so unhandled
        messages are re-delivered rather than silently skipped.
        """
        logger.info("Starting message consumption (at-least-once)...")

        try:
            while self.running:
                data = await self.consumer.getmany(timeout_ms=1000, max_records=10)

                if not data:
                    await asyncio.sleep(0.1)
                    continue

                msg_count = sum(len(messages) for messages in data.values())
                logger.debug(f"Received {msg_count} message(s) from Kafka")

                batch_handled = True
                for tp, messages in data.items():
                    for message in messages:
                        if not await self._handle_message(message):
                            batch_handled = False
                            break
                    if not batch_handled:
                        break

                if batch_handled:
                    # Advance committed offsets only after the whole batch is handled.
                    await self.consumer.commit()
                else:
                    # Rewind to the last committed offsets so nothing is skipped, then
                    # back off before retrying the batch.
                    await self._seek_to_committed()
                    await asyncio.sleep(self._retry_backoff)

        except asyncio.CancelledError:
            logger.info("Message consumption cancelled")
        except Exception as e:
            logger.error(f"Fatal error in consumer loop: {e}", exc_info=True)

    async def _handle_message(self, message) -> bool:
        """
        Process one message, retrying on failure and dead-lettering if it stays
        unprocessable. Returns True when the message has been handled and its offset may
        be committed, False when it could not be handled (so the offset must not advance).
        """
        for attempt in range(1, self._max_retries + 1):
            try:
                await self._process_message(message.value)
                return True
            except Exception as e:
                logger.error(
                    f"Error processing message (attempt {attempt}/{self._max_retries}): {e}",
                    exc_info=True,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_backoff * attempt)

        # Retries exhausted: route to the dead-letter topic.
        return await self._dead_letter(message)

    async def _dead_letter(self, message) -> bool:
        """
        Publish an unprocessable message to the dead-letter topic. Returns False if even
        that fails, so the offset is not committed and the message is retried later.
        """
        try:
            await self.producer.send_and_wait(
                self._dlq_topic,
                value={
                    "original": message.value,
                    "topic": message.topic,
                    "partition": message.partition,
                    "offset": message.offset,
                },
            )
            logger.error(
                f"Message dead-lettered to {self._dlq_topic} "
                f"(partition={message.partition}, offset={message.offset}) after exhausting retries"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to dead-letter message; offset will not advance: {e}", exc_info=True)
            return False

    async def _seek_to_committed(self):
        """
        Rewind every assigned partition to its last committed offset so unhandled
        messages are re-delivered instead of skipped.
        """
        for tp in self.consumer.assignment():
            committed = await self.consumer.committed(tp)
            if committed is not None:
                self.consumer.seek(tp, committed)
            else:
                await self.consumer.seek_to_beginning(tp)
    
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
            # Convert datetime objects to ISO format strings for JSON serialization
            serializable_alert = {}
            for key, value in alert.items():
                if isinstance(value, datetime):
                    serializable_alert[key] = value.isoformat()
                else:
                    serializable_alert[key] = value

            await self.producer.send_and_wait(
                self.config.KAFKA_TOPIC_ALERTS,
                value=serializable_alert
            )
            logger.info(f"Published alert for rule {alert['rule_id']}")
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
