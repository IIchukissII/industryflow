# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
from functools import lru_cache

class Settings:
    """Configuration for Ingestion Service - NO DEFAULTS.

    Ingestion is device-only and stateless: it authenticates producers by mTLS and produces
    to Kafka. It holds no database role and no JWT secret (ADR-0002).
    """

    # Server
    INGESTION_SERVICE_PORT: int = int(os.getenv("INGESTION_SERVICE_PORT"))

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
    KAFKA_TOPIC_SENSOR_DATA: str = os.getenv("KAFKA_TOPIC_SENSOR_DATA", "sensor-data-raw")

    # CORS
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")

    @property
    def kafka_bootstrap_servers(self) -> str:
        return self.KAFKA_BOOTSTRAP_SERVERS

@lru_cache()
def get_settings() -> Settings:
    return Settings()
