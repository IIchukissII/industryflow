# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The alert worker's consumer loop as a process the crash proof can kill
(test_kafka_consumer_integration.py).

    python kafka_crash_worker.py <bootstrap> <topic> <group> <log file> <hang seq> <dead-letter topic>

Each reading the engine is handed is appended to the log file as its `seq`. The reading whose `seq`
is <hang seq> is logged and then never finishes, so the batch holding it is never committed: the
process is killed with that batch in flight.
"""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "worker"))

from kafka_consumer import AlertKafkaConsumer  # noqa: E402


class HangingEngine:
    def __init__(self, log_path, hang_seq):
        self.log_path = log_path
        self.hang_seq = hang_seq

    async def evaluate(self, reading, company_id):
        with open(self.log_path, "a") as log:
            log.write(json.dumps(reading["seq"]) + "\n")
        if reading["seq"] == self.hang_seq:
            await asyncio.Event().wait()
        return []


async def main(bootstrap, topic, group, log_path, hang_seq, dlq):
    config = SimpleNamespace(
        KAFKA_BOOTSTRAP_SERVERS=bootstrap, KAFKA_TOPIC_SENSOR_DATA=topic, KAFKA_GROUP_ID=group,
        KAFKA_TOPIC_DLQ=dlq, KAFKA_MAX_RETRIES=1, KAFKA_RETRY_BACKOFF_SECONDS=0.05,
        KAFKA_MAX_BACKOFF_SECONDS=0.2, HEARTBEAT_FILE=f"{log_path}.heartbeat",
    )
    consumer = AlertKafkaConsumer(config, HangingEngine(log_path, hang_seq))
    await consumer.start()
    await consumer.consume_messages()


if __name__ == "__main__":
    bootstrap, topic, group, log_path, hang_seq, dlq = sys.argv[1:7]
    asyncio.run(main(bootstrap, topic, group, log_path, int(hang_seq), dlq))
