# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pytest fixtures for the Ingestion Service tests.

These are in-process functional tests of the HTTP request path: the real service code
(``main`` + ``dependencies``) runs under a Starlette TestClient with only Kafka mocked, so
no broker, database, or mTLS edge is required. The mTLS edge is simulated by forwarding the
``X-Client-Verify`` / ``X-Client-Cert`` headers it would set (ADR-0002).
"""
import datetime
import os
import sys

import pytest

# Import the service modules the same way the container does (flat layout, no package).
SERVICE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, SERVICE_DIR)

# config.py reads these at import time with no defaults — set them before importing the app.
os.environ.setdefault("INGESTION_SERVICE_PORT", "8003")
os.environ.setdefault("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
os.environ.setdefault("KAFKA_TOPIC_SENSOR_DATA", "sensor-data-raw")
os.environ.setdefault("CORS_ORIGINS", "*")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402


def make_device_cert(san_uri: str | None) -> str:
    """Build a self-signed device leaf certificate as PEM.

    The chain is irrelevant here: the mTLS edge verifies it against the device CA + CRL and
    only forwards it (ADR-0002 dec 2); the service merely parses the identity from the SAN.
    Pass ``san_uri=None`` to omit the SAN entirely.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "device")])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime(2025, 1, 1))
        .not_valid_after(datetime.datetime(2035, 1, 1))
    )
    if san_uri is not None:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.UniformResourceIdentifier(san_uri)]),
            critical=False,
        )
    cert = builder.sign(key, hashes.SHA256())
    return cert.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture
def make_cert():
    """Factory fixture returning :func:`make_device_cert`."""
    return make_device_cert


@pytest.fixture
def sample_body():
    """A minimal valid /ingest body. Note: it carries no company_id — the tenant is only
    ever taken from the verified certificate (ADR-0002 dec 6)."""
    import uuid

    return {
        "timestamp": "2026-06-27T10:30:00Z",
        "sensor_id": str(uuid.uuid4()),
        "equipment_id": str(uuid.uuid4()),
        "site_id": "factory_north",
        "value": 75.5,
        "unit": "celsius",
        "quality_code": 0,
    }


@pytest.fixture
def client(monkeypatch):
    """A TestClient with Kafka mocked.

    Yields ``(test_client, produced)`` where ``produced`` is a list that captures every
    message the service would have published to Kafka.
    """
    from fastapi.testclient import TestClient

    import kafka_producer
    import main

    produced: list[dict] = []

    async def fake_send(message, topic=None):
        produced.append(message)
        return True

    class FakeSingleton:
        @staticmethod
        async def get_producer():
            return object()

        @staticmethod
        async def close():
            return None

    # Patch both the producer module and the names main imported from it.
    monkeypatch.setattr(kafka_producer, "send_sensor_data", fake_send, raising=True)
    monkeypatch.setattr(kafka_producer, "AsyncKafkaProducerSingleton", FakeSingleton, raising=True)
    monkeypatch.setattr(main, "send_sensor_data", fake_send, raising=True)
    monkeypatch.setattr(main, "AsyncKafkaProducerSingleton", FakeSingleton, raising=True)

    with TestClient(main.app) as c:
        yield c, produced
