# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
from functools import lru_cache

from industryflow_contracts import tenant_status

# The header conventions a terminating mTLS edge uses to forward the verified client
# certificate (ADR-0002 dec 2): our compose nginx edge sets X-Client-*, ingress-nginx
# (ADR-0009) sets ssl-client-*. A deployment names exactly one; the other is foreign there.
DEVICE_IDENTITY_HEADER_CONVENTIONS = ("x-client", "ssl-client")


class Settings:
    """Configuration for Ingestion Service - NO DEFAULTS.

    Ingestion is device-only and stateless: it authenticates producers by mTLS, produces to
    Kafka and reads the tenant-status stream from it. It holds no database role, no JWT secret
    and no CORS policy (ADR-0002): no browser ever calls it.
    """

    # Server
    INGESTION_SERVICE_PORT: int = int(os.getenv("INGESTION_SERVICE_PORT"))

    # Kafka
    KAFKA_BOOTSTRAP_SERVERS: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS")
    KAFKA_TOPIC_SENSOR_DATA: str = os.getenv("KAFKA_TOPIC_SENSOR_DATA")

    # Which edge header convention carries the verified certificate in this deployment
    # (one of DEVICE_IDENTITY_HEADER_CONVENTIONS). Required: guessing it, or accepting
    # either, would let a header the edge does not set reach the identity check.
    DEVICE_IDENTITY_HEADERS: str = os.getenv("DEVICE_IDENTITY_HEADERS", "")

    # The tenant-status stream suspended tenants are refused from (ADR-0033 dec 3); the alert worker
    # publishes it under the same name. The default is the shared contract's.
    KAFKA_TOPIC_TENANT_STATUS: str = os.getenv("KAFKA_TOPIC_TENANT_STATUS", tenant_status.DEFAULT_TOPIC)

    def __init__(self):
        if not self.KAFKA_TOPIC_SENSOR_DATA:
            raise RuntimeError("KAFKA_TOPIC_SENSOR_DATA must be set")
        if self.DEVICE_IDENTITY_HEADERS not in DEVICE_IDENTITY_HEADER_CONVENTIONS:
            raise RuntimeError(
                "DEVICE_IDENTITY_HEADERS must be one of "
                f"{', '.join(DEVICE_IDENTITY_HEADER_CONVENTIONS)} (got {self.DEVICE_IDENTITY_HEADERS!r})"
            )

    @property
    def kafka_bootstrap_servers(self) -> str:
        return self.KAFKA_BOOTSTRAP_SERVERS

@lru_cache()
def get_settings() -> Settings:
    return Settings()
