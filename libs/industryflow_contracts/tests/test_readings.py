# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The device reading's shape and its device-side TLS (ADR-0002)."""
import ssl

import pytest

from industryflow_contracts import readings


def test_a_reading_carries_the_required_fields_and_only_the_optional_ones_given():
    body = readings.reading(timestamp="2026-11-08T10:00:00+00:00", sensor_id="s", equipment_id="e",
                            site_id="site", value=1)
    assert tuple(body) == readings.REQUIRED_FIELDS and body["value"] == 1.0
    full = readings.reading(timestamp="t", sensor_id="s", equipment_id="e", site_id="x", value=2.5,
                            unit="bar", quality_code=0)
    assert tuple(full) == readings.REQUIRED_FIELDS + readings.OPTIONAL_FIELDS


def test_a_reading_names_no_tenant():
    # The tenant is the certificate's (ADR-0002 dec 3).
    assert not any("company" in f or "tenant" in f for f in readings.REQUIRED_FIELDS + readings.OPTIONAL_FIELDS)


def test_every_field_is_named_by_the_caller():
    with pytest.raises(TypeError):
        readings.reading(sensor_id="s", equipment_id="e", site_id="x", value=1.0)


def test_the_device_context_verifies_the_edge(tmp_path):
    from datetime import datetime, timedelta, timezone

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "device")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + timedelta(days=1))
            .sign(key, hashes.SHA256()))
    (tmp_path / "c.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (tmp_path / "k.pem").write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                        serialization.NoEncryption()))
    ctx = readings.device_tls_context(str(tmp_path / "c.pem"), str(tmp_path / "k.pem"), str(tmp_path / "c.pem"))
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
