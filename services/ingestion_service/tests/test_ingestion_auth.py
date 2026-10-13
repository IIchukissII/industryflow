# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Functional tests for the ingestion request path (ADR-0002).

Ingestion is device-only and mTLS-only: the tenant is read from the verified client
certificate the edge forwards, never from the request body, and there is no JWT/bearer
fallback and no database lookup. These tests pin that contract.
"""
import uuid
from urllib.parse import quote

import pytest


def _headers(pem: str, verify: str = "SUCCESS", *, edge: str = "x"):
    """Build the verified-cert headers an mTLS edge forwards.

    ``edge='x'`` uses the compose nginx convention (X-Client-*); ``edge='ssl'`` uses the
    ingress-nginx convention (ssl-client-*). The cert is URL-escaped, as nginx forwards
    ``$ssl_client_escaped_cert``.
    """
    prefix = "x-client" if edge == "x" else "ssl-client"
    return {f"{prefix}-verify": verify, f"{prefix}-cert": quote(pem)}


def test_health(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "ingestion"


def test_valid_mtls_accepts_and_uses_cert_tenant(client, make_cert, sample_body):
    c, produced = client
    tenant = str(uuid.uuid4())
    pem = make_cert(f"industryflow:tenant/{tenant}/device/gw-01")

    r = c.post("/ingest", json=sample_body, headers=_headers(pem))

    assert r.status_code == 202
    assert r.json()["company_id"] == tenant
    # The tenant on the produced message comes from the certificate, not the body.
    assert len(produced) == 1
    assert produced[0]["company_id"] == tenant
    assert produced[0]["sensor_id"] == sample_body["sensor_id"]
    assert produced[0]["site_id"] == sample_body["site_id"]


def test_ingress_nginx_convention_when_configured(client, edge, make_cert, sample_body):
    c, produced = client
    edge("ssl-client")
    tenant = str(uuid.uuid4())
    pem = make_cert(f"industryflow:tenant/{tenant}/device/gw-01")

    r = c.post("/ingest", json=sample_body, headers=_headers(pem, edge="ssl"))

    assert r.status_code == 202
    assert produced[0]["company_id"] == tenant


# --- Header smuggling (ADR-0002 dec 3) -------------------------------------------------------
# Each edge overwrites only its OWN convention's headers. Anything of the other convention
# reached the service because the client sent it, so it must never be read as an identity.

def test_smuggled_x_client_headers_rejected_behind_ingress_nginx(client, edge, make_cert, sample_body):
    # Tenant-A device, verified by ingress-nginx (ssl-client-*), smuggles a tenant-B identity
    # in X-Client-*, which ingress-nginx passes through untouched.
    c, produced = client
    edge("ssl-client")
    own = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-a")
    forged = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-b")
    headers = {**_headers(own, edge="ssl"), **_headers(forged, edge="x")}

    r = c.post("/ingest", json=sample_body, headers=headers)

    assert r.status_code == 400
    assert produced == []


def test_smuggled_x_client_headers_alone_rejected_behind_ingress_nginx(client, edge, make_cert, sample_body):
    c, produced = client
    edge("ssl-client")
    forged = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-b")

    r = c.post("/ingest", json=sample_body, headers=_headers(forged, edge="x"))

    assert r.status_code == 400
    assert produced == []


def test_smuggled_ssl_client_headers_rejected_behind_compose_edge(client, edge, make_cert, sample_body):
    c, produced = client
    edge("x-client")
    own = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-a")
    forged = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-b")
    headers = {**_headers(own, edge="x"), **_headers(forged, edge="ssl")}

    r = c.post("/ingest", json=sample_body, headers=headers)

    assert r.status_code == 400
    assert produced == []


def test_any_foreign_prefixed_header_is_refused(client, edge, make_cert, sample_body):
    # Not just the two names this service reads: e.g. ingress-nginx's ssl-client-subject-dn.
    c, produced = client
    edge("x-client")
    own = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-a")
    headers = {**_headers(own, edge="x"), "SSL-Client-Subject-DN": "CN=forged"}

    r = c.post("/ingest", json=sample_body, headers=headers)

    assert r.status_code == 400
    assert produced == []


def test_unknown_convention_fails_at_startup(monkeypatch):
    import config

    for bad in ("", "x-client,ssl-client", "X-Forwarded"):
        monkeypatch.setattr(config.Settings, "DEVICE_IDENTITY_HEADERS", bad)
        with pytest.raises(RuntimeError, match="DEVICE_IDENTITY_HEADERS"):
            config.Settings()


def test_no_cors_on_the_device_only_service(client):
    # No browser calls ingestion; a credentialed cross-origin preflight gets no CORS grant.
    c, _ = client
    r = c.options(
        "/ingest",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in r.headers
    assert "access-control-allow-credentials" not in r.headers


def test_internal_error_does_not_leak_detail(client, make_cert, sample_body, monkeypatch):
    import main

    async def boom(message, topic=None):
        raise RuntimeError("broker kafka:29092 unreachable")

    monkeypatch.setattr(main, "send_sensor_data", boom)
    c, _ = client
    pem = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-01")
    r = c.post("/ingest", json=sample_body, headers=_headers(pem))
    assert r.status_code == 500
    assert "kafka" not in r.text
    assert "unreachable" not in r.text


def test_no_certificate_is_unauthorized(client, sample_body):
    # The JWT/bearer fallback was removed: with no forwarded cert there is no way in.
    c, produced = client
    r = c.post("/ingest", json=sample_body)
    assert r.status_code == 401
    assert produced == []


def test_bearer_token_alone_is_rejected(client, sample_body):
    # A stale bearer token must not authenticate ingestion anymore (JWT path removed).
    c, _ = client
    r = c.post("/ingest", json=sample_body, headers={"Authorization": "Bearer faketoken"})
    assert r.status_code == 401


def test_unverified_certificate_is_unauthorized(client, make_cert, sample_body):
    c, _ = client
    pem = make_cert(f"industryflow:tenant/{uuid.uuid4()}/device/gw-01")
    r = c.post("/ingest", json=sample_body, headers=_headers(pem, verify="FAILED"))
    assert r.status_code == 401


def test_certificate_without_industryflow_identity_is_forbidden(client, make_cert, sample_body):
    c, _ = client
    pem = make_cert("spiffe://example.org/some/other/identity")
    r = c.post("/ingest", json=sample_body, headers=_headers(pem))
    assert r.status_code == 403


def test_non_uuid_tenant_is_forbidden(client, make_cert, sample_body):
    # SAN shape matches the regex but the tenant fails UUID validation (anti-injection).
    c, _ = client
    pem = make_cert("industryflow:tenant/" + ("a" * 36) + "/device/gw-01")
    r = c.post("/ingest", json=sample_body, headers=_headers(pem))
    assert r.status_code == 403


def test_malformed_certificate_is_unauthorized(client, sample_body):
    c, _ = client
    r = c.post("/ingest", json=sample_body, headers={"x-client-verify": "SUCCESS", "x-client-cert": quote("not a certificate")})
    assert r.status_code == 401


def test_stats_requires_certificate(client):
    c, _ = client
    assert c.get("/stats").status_code == 401


def test_stats_reports_mtls_identity(client, make_cert):
    c, _ = client
    tenant = str(uuid.uuid4())
    pem = make_cert(f"industryflow:tenant/{tenant}/device/gw-07")
    r = c.get("/stats", headers=_headers(pem))
    assert r.status_code == 200
    body = r.json()
    assert body["company_id"] == tenant
    assert body["device_id"] == "gw-07"
    assert body["auth"] == "mtls"
