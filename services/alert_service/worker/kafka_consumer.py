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
import os
from typing import Optional
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer, TopicPartition
from aiokafka.errors import (
    CommitFailedError,
    IllegalGenerationError,
    InvalidProducerEpoch,
    KafkaError,
    OutOfOrderSequenceNumber,
    ProducerFenced,
    RebalanceInProgressError,
    UnknownMemberIdError,
    UnknownProducerId,
)

from errors import MessageValidationError, is_infrastructure_error

logger = logging.getLogger(__name__)

# Outcomes of handling one message.
HANDLED = "handled"          # processed, or dead-lettered: its offset may be committed
RETRY_BATCH = "retry_batch"  # not handled: rewind to the last commit and try again later

# Errors that stay with the producer id: every later send to the partition fails the same way until a
# new producer, under a new id, replaces it (send).
STICKY_PRODUCER_ERRORS = (OutOfOrderSequenceNumber, ProducerFenced, InvalidProducerEpoch, UnknownProducerId)
# Bounds on one send and on replacing a producer. An idempotent batch is retried until it is written, and
# a producer whose sender has ended never completes a send; neither may hold a caller for good.
SEND_TIMEOUT_SECONDS = 60
PRODUCER_START_TIMEOUT_SECONDS = 30
PRODUCER_STOP_TIMEOUT_SECONDS = 10


def sender_ended(producer) -> bool:
    """Whether aiokafka's sender task, which delivers every batch, has ended. Once it has, each pending
    and later send fails or never completes. aiokafka 0.15 exposes no public check; the attribute path
    is pinned by test_kafka_consumer.py against the real class."""
    task = getattr(getattr(producer, "_sender", None), "sender_task", None)
    return task is not None and task.done()


# Names this worker in the dead letters it writes, as spark-streaming names itself in its own.
DEAD_LETTER_CONSUMER = "alert-service-detector"


async def _stop_quietly(producer) -> None:
    try:
        await asyncio.wait_for(producer.stop(), PRODUCER_STOP_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - a broken or unstarted producer; nothing left to save
        pass


def _is_recoverable_commit_error(exc: BaseException) -> bool:
    """A commit that failed because the group moved under us (rebalance, fenced generation) or
    because the coordinator was briefly unreachable. The batch is simply redelivered: alert writes
    are idempotent (ADR-0005 dec 6), so re-processing is safe."""
    if isinstance(exc, (CommitFailedError, RebalanceInProgressError,
                        IllegalGenerationError, UnknownMemberIdError)):
        return True
    return isinstance(exc, KafkaError) and bool(getattr(exc, "retriable", False))


def decode_message(raw: Optional[bytes]) -> dict:
    """Deserialize one record's value. Malformed input is a validation error (dead-lettered),
    never an exception that escapes the consumer loop."""
    if raw is None:
        raise MessageValidationError("record has no value")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise MessageValidationError(f"record is not UTF-8 JSON: {e}") from None
    if not isinstance(value, dict):
        raise MessageValidationError(f"record is not a JSON object: {type(value).__name__}")
    return value


class AlertKafkaConsumer:
    """Kafka consumer for sensor data with schema-per-tenant routing"""

    def __init__(self, config, rules_engine):
        self.config = config
        self.rules_engine = rules_engine
        self.consumer: Optional[AIOKafkaConsumer] = None
        self.producer: Optional[AIOKafkaProducer] = None
        self.running = False
        # At-least-once handling parameters (ADR-0005), all from config.
        self._max_retries = max(1, config.KAFKA_MAX_RETRIES)
        self._retry_backoff = config.KAFKA_RETRY_BACKOFF_SECONDS
        self._max_backoff = config.KAFKA_MAX_BACKOFF_SECONDS
        self._dlq_topic = config.KAFKA_TOPIC_DLQ
        self._heartbeat_file = config.HEARTBEAT_FILE
        # Consecutive batches that could not be handled; drives the exponential backoff.
        self._failed_batches = 0
        # Serialises replacing a broken producer (send) between the dead-letter and status senders.
        self._producer_lock = asyncio.Lock()
        self.producers_replaced = 0

    async def start(self):
        """Initialize Kafka consumer and producer"""
        logger.info("Starting Kafka consumer...")

        # Create consumer. At-least-once (ADR-0005): start from the earliest offset so a
        # fresh consumer does not skip the backlog, and commit manually only after a batch
        # is fully handled (enable_auto_commit=False). Values stay raw bytes: each record is
        # decoded inside _handle_message, so one malformed record is dead-lettered instead of
        # raising out of getmany() and stopping the loop.
        self.consumer = AIOKafkaConsumer(
            self.config.KAFKA_TOPIC_SENSOR_DATA,
            bootstrap_servers=self.config.KAFKA_BOOTSTRAP_SERVERS,
            group_id=self.config.KAFKA_GROUP_ID,
            auto_offset_reset='earliest',
            enable_auto_commit=False
        )

        # Producer for the dead-letter topic and the tenant-status stream. acks='all' + idempotence
        # so a message is durably replicated before it counts as sent (ADR-0005). No serializer:
        # it sends bytes, and each sender encodes its own contract (the tenant status arrives
        # encoded by industryflow_contracts.tenant_status).
        self.producer = self._new_producer()

        await self.consumer.start()
        await self.producer.start()

        logger.info(f"Connected to Kafka: {self.config.KAFKA_BOOTSTRAP_SERVERS}")
        logger.info(f"Consuming from: {self.config.KAFKA_TOPIC_SENSOR_DATA}")
        logger.info(f"Dead-lettering to: {self._dlq_topic}")

        self.running = True
        self._beat()

    def _beat(self):
        """Liveness heartbeat: healthcheck.py fails the container once this file goes stale, so a
        loop that hangs or dies is restarted instead of reporting healthy."""
        try:
            with open(self._heartbeat_file, "a"):
                os.utime(self._heartbeat_file, None)
        except OSError as e:
            logger.warning(f"Could not touch heartbeat file {self._heartbeat_file}: {e}")

    async def consume_messages(self):
        """
        Main consumption loop with at-least-once semantics (ADR-0005).

        Each getmany batch is processed in full and only then committed. If any message
        cannot be handled (processed, or dead-lettered), the batch is not committed and the
        consumer is rewound to the last committed offsets, so unhandled messages are
        re-delivered rather than silently skipped.

        A commit lost to a rebalance rewinds the same way and carries on. Any other error from
        Kafka propagates: the loop ends, main exits non-zero and the container is restarted,
        instead of the process staying up with nothing consuming.
        """
        logger.info("Starting message consumption (at-least-once)...")

        while self.running:
            self._beat()
            data = await self.consumer.getmany(timeout_ms=1000, max_records=10)

            if not data:
                continue

            msg_count = sum(len(messages) for messages in data.values())
            logger.debug(f"Received {msg_count} message(s) from Kafka")

            if await self._handle_batch(data):
                self._failed_batches = 0
            else:
                await self._rewind_and_back_off()

    async def _handle_batch(self, data) -> bool:
        """Handle and commit one batch. False when it must be redelivered."""
        offsets = {}
        for tp, messages in data.items():
            for message in messages:
                if await self._handle_message(message) != HANDLED:
                    return False
                offsets[TopicPartition(tp.topic, tp.partition)] = message.offset + 1

        # Advance committed offsets only after the whole batch is handled.
        try:
            await self.consumer.commit(offsets)
        except Exception as e:
            if not _is_recoverable_commit_error(e):
                raise
            logger.warning(f"Commit failed ({type(e).__name__}: {e}); the batch will be redelivered")
            return False
        return True

    async def _rewind_and_back_off(self):
        """Rewind to the last commit, then wait. The wait doubles per consecutive failed batch up
        to KAFKA_MAX_BACKOFF_SECONDS, so an outage is retried patiently, not in a hot loop."""
        self._failed_batches += 1
        await self._seek_to_committed()
        delay = min(self._retry_backoff * (2 ** (self._failed_batches - 1)), self._max_backoff)
        await asyncio.sleep(delay)

    async def _handle_message(self, message) -> str:
        """
        Process one message. Returns HANDLED when its offset may be committed, RETRY_BATCH when
        it may not.

        - malformed or invalid reading: dead-lettered at once (no retry can fix it);
        - infrastructure failure (database, pool, network): never dead-lettered, the batch is
          rewound and retried until the dependency is back;
        - anything else: retried KAFKA_MAX_RETRIES times, then dead-lettered.
        """
        try:
            sensor_data = decode_message(message.value)
        except MessageValidationError as e:
            return await self._dead_letter(message, reason=str(e))

        last_error = None
        for attempt in range(1, self._max_retries + 1):
            try:
                await self._process_message(sensor_data)
                return HANDLED
            except MessageValidationError as e:
                return await self._dead_letter(message, reason=str(e))
            except Exception as e:
                if is_infrastructure_error(e):
                    logger.error(
                        f"Infrastructure error processing message (partition={message.partition}, "
                        f"offset={message.offset}); not dead-lettering, will retry: {e}"
                    )
                    return RETRY_BATCH
                logger.error(
                    f"Error processing message (attempt {attempt}/{self._max_retries}): {e}",
                    exc_info=True,
                )
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_backoff * attempt)
                last_error = e

        # Retries exhausted: route to the dead-letter topic.
        return await self._dead_letter(message, reason=f"retries exhausted: {last_error}")

    async def _dead_letter(self, message, *, reason: str) -> str:
        """
        Publish an unprocessable message to the dead-letter topic. Returns RETRY_BATCH if even
        that fails, so the offset is not committed and the message is retried later.
        """
        raw = message.value
        try:
            original = raw.decode("utf-8") if raw is not None else None
        except UnicodeDecodeError:
            original = raw.hex()
        try:
            await self.send(
                self._dlq_topic,
                # The envelope both dead-letter producers write (docs/operations/dead-letters.md).
                value=json.dumps({
                    "original": original,
                    "reason": reason,
                    "consumer": DEAD_LETTER_CONSUMER,
                    "topic": message.topic,
                    "partition": message.partition,
                    "offset": message.offset,
                }).encode("utf-8"),
            )
            logger.error(
                f"Message dead-lettered to {self._dlq_topic} "
                f"(partition={message.partition}, offset={message.offset}): {reason}"
            )
            return HANDLED
        except Exception as e:
            logger.error(f"Failed to dead-letter message; offset will not advance: {e}", exc_info=True)
            return RETRY_BATCH

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
        # Extract company_id for schema routing. Ingestion stamps it from the device certificate
        # (ADR-0002); a reading without one has no tenant and cannot be evaluated anywhere.
        company_id = sensor_data.get('company_id')
        if not company_id:
            raise MessageValidationError("reading has no company_id")

        logger.debug(f"Processing sensor={sensor_data.get('sensor_id')}, company={company_id}")

        # Evaluate rules (schema routing happens in rules_engine); the alerts are written to the
        # tenant's schema, which is where every consumer of them reads.
        triggered_alerts = await self.rules_engine.evaluate(sensor_data, company_id)

        if triggered_alerts:
            logger.info(f"Triggered {len(triggered_alerts)} alert(s) for company {company_id}")

    def _new_producer(self) -> AIOKafkaProducer:
        return AIOKafkaProducer(
            bootstrap_servers=self.config.KAFKA_BOOTSTRAP_SERVERS,
            acks='all',
            enable_idempotence=True
        )

    async def send(self, topic: str, value: bytes, key: Optional[bytes] = None):
        """Send one message on the worker's producer, the one the dead letters and the tenant-status
        stream share.

        aiokafka's idempotent producer numbers a batch before it is sent and drops a batch that outlives
        its timeout with no partition leader, as during a broker restart. The broker then refuses every
        later number on that partition with OutOfOrderSequenceNumber: the producer id has lost its
        sequence there for good. That and the other errors that stay with a producer id
        (STICKY_PRODUCER_ERRORS), and a producer whose sender task has ended, are cured only by a new
        producer under a new id: the broken one is replaced and the message sent once more on the new."""
        producer = self.producer
        if sender_ended(producer):
            await self._replace_producer(producer, "its sender ended")
            producer = self.producer
        try:
            return await self._send_on(producer, topic, value, key)
        except STICKY_PRODUCER_ERRORS as e:
            reason = type(e).__name__
        except (asyncio.TimeoutError, KafkaError):
            if not sender_ended(producer):
                raise
            reason = "its sender ended"
        await self._replace_producer(producer, reason)
        return await self._send_on(self.producer, topic, value, key)

    @staticmethod
    async def _send_on(producer, topic, value, key):
        return await asyncio.wait_for(producer.send_and_wait(topic, value=value, key=key), SEND_TIMEOUT_SECONDS)

    async def _replace_producer(self, broken: AIOKafkaProducer, reason: str) -> None:
        async with self._producer_lock:
            if self.producer is not broken:  # another sender replaced it already
                return
            logger.warning("replacing the Kafka producer: %s", reason)
            # The broken one stays until a new one has started: if the broker refuses the start, the
            # next send fails the same way and tries again.
            fresh = self._new_producer()
            try:
                await asyncio.wait_for(fresh.start(), PRODUCER_START_TIMEOUT_SECONDS)
            except BaseException:
                await _stop_quietly(fresh)
                raise
            self.producer = fresh
            self.producers_replaced += 1
            # Its flush can wait on batches its dead sender never delivers.
            await _stop_quietly(broken)

    async def stop(self):
        """Stop Kafka consumer and producer"""
        logger.info("Stopping Kafka consumer...")
        self.running = False

        if self.consumer:
            await self.consumer.stop()
        if self.producer:
            await self.producer.stop()

        logger.info("Kafka consumer stopped")
