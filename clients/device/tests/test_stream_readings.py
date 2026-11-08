# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The sample device (ADR-0002): its arguments, its values, and a real mutual-TLS exchange.

The edge is stood in by a local HTTPS server that requires a client certificate from the test CA, as
the ingest edge does; the device verifies the server against the same CA, host name included.
"""
import argparse
import http.server
import json
import ssl
import sys
import threading
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stream_readings as device  # noqa: E402
from industryflow_contracts import readings  # noqa: E402

SID = "2e9bf157-d0f0-4618-9286-c792ee54c127"
EID = "ef9791ba-4fba-4d78-ab76-eb7094933f99"


def test_a_sensor_is_a_name_an_id_and_a_range():
    assert device.parse_sensor(f"flow_rate={SID}:30:50") == device.Sensor("flow_rate", SID, 30.0, 50.0)
    for bad in ("flow_rate", f"flow_rate={SID}", f"flow_rate={SID}:50:30", f"={SID}:1:2", "x=:1:2"):
        with pytest.raises(argparse.ArgumentTypeError):
            device.parse_sensor(bad)


def test_values_stay_inside_the_range_and_move():
    import random
    sensor = device.Sensor("p", SID, 6.0, 9.0)
    rng = random.Random(1)
    values = [device.value(sensor, step, rng) for step in range(120)]
    assert all(6.0 <= v <= 9.0 for v in values)
    assert max(values) - min(values) > 1.0


def test_each_round_sends_one_contract_reading_per_sensor_and_counts_refusals():
    sent, statuses = [], iter([202, 202, 503, 202])

    def send(url, body, context):
        sent.append(body)
        return next(statuses)

    sensors = [device.Sensor("a", SID, 0, 1), device.Sensor("b", SID, 0, 1)]
    clock = iter([datetime(2026, 11, 8, 10, 0, tzinfo=timezone.utc), datetime(2026, 11, 8, 10, 0, 1, tzinfo=timezone.utc)])
    refused = device.stream("u", None, EID, "site", sensors, 1.0, 2, send=send, clock=lambda: next(clock),
                            sleep=lambda s: None, out=open("/dev/null", "w"))
    assert refused == 1
    assert [b["timestamp"] for b in sent] == ["2026-11-08T10:00:00+00:00"] * 2 + ["2026-11-08T10:00:01+00:00"] * 2
    assert all(tuple(b) == readings.REQUIRED_FIELDS and b["equipment_id"] == EID for b in sent)


# --- a real exchange over mutual TLS -------------------------------------------------------------

def _pki(tmp_path):
    now = datetime.now(timezone.utc)

    def make(name, issuer=None, issuer_key=None, ca=False, san=None):
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        b = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer or subject)
             .public_key(key.public_key()).serial_number(x509.random_serial_number())
             .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
             .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
             .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(
                 (issuer_key or key).public_key()), critical=False))
        if ca:
            b = b.add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                              content_commitment=False, key_encipherment=False,
                                              data_encipherment=False, key_agreement=False,
                                              encipher_only=False, decipher_only=False), critical=True)
        if san:
            b = b.add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        cert = b.sign(issuer_key or key, hashes.SHA256())
        cp, kp = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
        cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
        return cert, key, str(cp), str(kp)

    ca, ca_key, ca_crt, _ = make("test-ca", ca=True)
    _, _, srv_crt, srv_key = make("edge", ca.subject, ca_key, san="localhost")
    _, _, dev_crt, dev_key = make("device", ca.subject, ca_key)
    other, other_key, other_ca, _ = make("other-ca", ca=True)
    _, _, stranger_crt, stranger_key = make("stranger", other.subject, other_key)
    return dict(ca=ca_crt, srv=(srv_crt, srv_key), dev=(dev_crt, dev_key), stranger=(stranger_crt, stranger_key))


@pytest.fixture
def edge(tmp_path):
    pki = _pki(tmp_path)
    received = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.ThreadingHTTPServer(("localhost", 0), Handler)
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=pki["ca"])
    ctx.verify_mode = ssl.CERT_REQUIRED
    ctx.load_cert_chain(*pki["srv"])
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"https://localhost:{server.server_address[1]}/ingest", pki, received
    server.shutdown()


def test_the_device_streams_over_mutual_tls(edge, capsys):
    url, pki, received = edge
    rc = device.main(["--url", url, "--cert", pki["dev"][0], "--key", pki["dev"][1], "--ca", pki["ca"],
                      "--equipment-id", EID, "--site-id", "plant", "--sensor", f"flow={SID}:30:50",
                      "--interval", "0", "--count", "3"])
    assert rc == 0, capsys.readouterr().out
    assert len(received) == 3 and all(r["equipment_id"] == EID and 30 <= r["value"] <= 50 for r in received)


def test_an_edge_the_device_cannot_verify_is_not_sent_to(edge, tmp_path):
    url, pki, received = edge
    (tmp_path / "other").mkdir()
    other = _pki(tmp_path / "other")
    with pytest.raises(urllib.error.URLError, match="CERTIFICATE_VERIFY_FAILED"):
        device.main(["--url", url, "--cert", pki["dev"][0], "--key", pki["dev"][1], "--ca", other["ca"],
                     "--equipment-id", EID, "--site-id", "plant", "--sensor", f"flow={SID}:30:50", "--count", "1"])
    assert received == []


def test_a_device_the_edge_does_not_trust_is_refused_in_the_handshake(edge):
    url, pki, received = edge
    with pytest.raises((urllib.error.URLError, ConnectionError, ssl.SSLError)):
        device.main(["--url", url, "--cert", pki["stranger"][0], "--key", pki["stranger"][1], "--ca", pki["ca"],
                     "--equipment-id", EID, "--site-id", "plant", "--sensor", f"flow={SID}:30:50", "--count", "1"])
    assert received == []
