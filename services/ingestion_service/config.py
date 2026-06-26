# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
from functools import lru_cache

class Settings:
    """Configuration for Ingestion Service - NO DEFAULTS"""
    
    # Server
    INGESTION_SERVICE_PORT: int = int(os.getenv("INGESTION_SERVICE_PORT"))
    
    # Database (for JWT user lookup)
    DB_HOST: str = os.getenv("DB_HOST")
    DB_PORT: int = int(os.getenv("DB_PORT"))
    DB_NAME: str = os.getenv("DB_NAME")
    INGESTION_SERVICE_DB_USER: str = os.getenv("INGESTION_SERVICE_DB_USER")
    INGESTION_SERVICE_DB_PASSWORD: str = os.getenv("INGESTION_SERVICE_DB_PASSWORD")
    
    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
    KAFKA_TOPIC_RAW: str = os.getenv("KAFKA_TOPIC_RAW", "sensor-data-raw")
    
    # JWT
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    
    # CORS
    CORS_ORIGINS: str = os.getenv("CORS_ORIGINS", "*")
    
    @property
    def kafka_bootstrap_servers(self) -> str:
        return self.KAFKA_BOOTSTRAP_SERVERS

@lru_cache()
def get_settings() -> Settings:
    return Settings()
