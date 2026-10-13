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

from industryflow_contracts import tenancy

from config import DEVICE_IDENTITY_HEADER_CONVENTIONS, get_settings

# Device identity URI SAN: industryflow:tenant/<company_uuid>/device/<device_id> (ADR-0007 dec 3).
DEVICE_SAN_RE = re.compile(r"^industryflow:tenant/([0-9a-fA-F-]{36})/device/(.+)$")


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
            # UUID-validate before the tenant reaches schema resolution (raises ValueError), and
            # route by the canonical form only (ADR-0003 dec 4).
            company_id = tenancy.canonical_company_id(company_id)
            return {"company_id": company_id, "device_id": device_id, "source": "mtls"}

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Certificate has no IndustryFlow device identity")


async def get_ingestion_identity(request: Request) -> dict:
    """
    Resolve the tenant for an ingested write from the verified device certificate (ADR-0002).

    The terminating mTLS edge verifies the client certificate and forwards it in the header
    pair of this deployment's convention (``DEVICE_IDENTITY_HEADERS``): ``<prefix>-verify``
    and ``<prefix>-cert``. The edge sets those itself, overwriting anything the client sent.
    Only that convention is read. A request carrying the other convention's headers got them
    from the client, because no edge in this deployment sets them, so it is refused outright
    (ADR-0002 dec 3). The tenant is read from the verified SAN, never from the request body.
    """
    prefix = get_settings().DEVICE_IDENTITY_HEADERS
    foreign = tuple(f"{p}-" for p in DEVICE_IDENTITY_HEADER_CONVENTIONS if p != prefix)
    if any(name.lower().startswith(foreign) for name in request.headers.keys()):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unexpected client identity header")

    verify = request.headers.get(f"{prefix}-verify")
    if verify is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No device certificate")
    if verify != "SUCCESS":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Client certificate not verified")

    cert = request.headers.get(f"{prefix}-cert")
    if not cert:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing client certificate")
    try:
        return _identity_from_client_cert(cert)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Certificate tenant is not a valid UUID")
