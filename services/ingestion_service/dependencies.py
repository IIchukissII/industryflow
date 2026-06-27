# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication dependencies for Ingestion Service.

Ingestion is device-only: producers authenticate with mTLS and the tenant is read from the
verified client-certificate SAN (ADR-0002). The service holds no database role and never
queries the database — it only parses the certificate the edge has already verified and
produces to Kafka.
"""
from fastapi import HTTPException, status, Request
from urllib.parse import unquote
from cryptography import x509
import re
import uuid

# Device identity URI SAN: industryflow:tenant/<company_uuid>/device/<device_id> (ADR-0007 dec 3).
DEVICE_SAN_RE = re.compile(r"^industryflow:tenant/([0-9a-fA-F-]{36})/device/(.+)$")


def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Validate a company_id as a UUID and return its tenant schema name.

    The UUID validation is the security-relevant part here: a non-UUID raises ValueError
    instead of flowing on toward tenant routing (defends against injection — see ADR-0003).
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


def _identity_from_client_cert(escaped_pem: str) -> dict:
    """
    Read tenant + device from the verified device certificate the mTLS edge forwarded.

    The edge has already verified the certificate against the device CA and CRL (ADR-0002
    dec 2); here we only PARSE the identity from its URI SAN and UUID-validate the tenant
    before it is used to route a write (ADR-0002 dec 6 / ADR-0003).
    """
    try:
        cert = x509.load_pem_x509_certificate(unquote(escaped_pem).encode())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        uris = san.get_values_for_type(x509.UniformResourceIdentifier)
    except Exception:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Malformed client certificate")

    for uri in uris:
        m = DEVICE_SAN_RE.match(uri)
        if m:
            company_id, device_id = m.group(1), m.group(2)
            # UUID-validate before the tenant reaches schema resolution (raises ValueError).
            normalize_company_id_to_schema(company_id)
            return {"company_id": str(uuid.UUID(company_id)), "device_id": device_id, "source": "mtls"}

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Certificate has no IndustryFlow device identity")


async def get_ingestion_identity(request: Request) -> dict:
    """
    Resolve the tenant for an ingested write from the verified device certificate (ADR-0002).

    The terminating mTLS edge verifies the client certificate and forwards it in
    X-Client-Verify / X-Client-Cert; the tenant is read from the verified SAN and is never
    taken from the request body.
    """
    # Accept either edge's header convention: our compose nginx edge sends X-Client-*,
    # while ingress-nginx (ADR-0009) sends ssl-client-* — same verified-cert meaning.
    verify = request.headers.get("x-client-verify") or request.headers.get("ssl-client-verify")
    if verify is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No device certificate")
    if verify != "SUCCESS":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Client certificate not verified")

    cert = request.headers.get("x-client-cert") or request.headers.get("ssl-client-cert")
    if not cert:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing client certificate")
    try:
        return _identity_from_client_cert(cert)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Certificate tenant is not a valid UUID")
