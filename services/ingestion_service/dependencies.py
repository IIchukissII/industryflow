# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Authentication dependencies for Ingestion Service.
"""
from jose import JWTError, jwt
import asyncpg
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from urllib.parse import unquote
from cryptography import x509
from config import get_settings
import re
import uuid

settings = get_settings()
security = HTTPBearer()
# Optional bearer for the unified ingestion dependency: a device request carries no
# Authorization header, so auto_error must not 401 before we check for a client cert.
security_optional = HTTPBearer(auto_error=False)

# Device identity URI SAN: industryflow:tenant/<company_uuid>/device/<device_id> (ADR-0007 dec 3).
DEVICE_SAN_RE = re.compile(r"^industryflow:tenant/([0-9a-fA-F-]{36})/device/(.+)$")

# Database connection pool (will be set in startup)
db_pool: Optional[asyncpg.Pool] = None

def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Convert a company_id UUID to its tenant schema name.

    company_id is validated as a UUID first, so the result is safe to interpolate into a
    SET search_path statement; a non-UUID raises ValueError instead of reaching SQL
    (defends against injection — see ADR-0003).
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"

async def get_current_user_with_company(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    Verify JWT token and resolve user's company_id.
    Returns dict with user_id and company_id.
    """
    token = credentials.credentials
    
    try:
        # Decode JWT
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_aud": False}
        )
        
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: missing user_id"
            )

        # Prefer the company_id claim from the JWT (ADR-0003 dec 2); avoids the per-request
        # tenant-schema scan below (review finding X2). Fall back to the scan for legacy tokens.
        claim = payload.get("company_id")
        if claim:
            return {"user_id": user_id, "company_id": str(claim)}

        # Resolve company_id from database
        if not db_pool:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Database pool not initialized"
            )
        
        async with db_pool.acquire() as conn:
            # Search all tenant schemas for user
            schemas = await conn.fetch("""
                SELECT schema_name FROM information_schema.schemata 
                WHERE schema_name LIKE 'tenant_%'
            """)
            
            for schema_row in schemas:
                schema_name = schema_row['schema_name']
                await conn.execute(f"SET search_path TO {schema_name}, public")
                
                company_id = await conn.fetchval(
                    'SELECT company_id FROM "user" WHERE id = $1',
                    user_id
                )
                
                if company_id:
                    return {
                        "user_id": user_id,
                        "company_id": str(company_id)
                    }
        
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found in any tenant"
        )
        
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {str(e)}"
        )


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


async def get_ingestion_identity(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
) -> dict:
    """
    Resolve the tenant for an ingested write (ADR-0002).

    Devices authenticate by mTLS: the terminating edge verifies the client certificate and
    forwards it in X-Client-Verify / X-Client-Cert, and the tenant is read from the verified
    SAN. The JWT bearer path is retained ONLY as a labelled transition (dec 8) and is removed
    once producers are on certificates.
    """
    verify = request.headers.get("x-client-verify")
    if verify is not None:
        if verify != "SUCCESS":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Client certificate not verified")
        cert = request.headers.get("x-client-cert")
        if not cert:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing client certificate")
        try:
            return _identity_from_client_cert(cert)
        except ValueError:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Certificate tenant is not a valid UUID")

    # Transition: JWT ingestion (ADR-0002 dec 8) — removed once producers present certificates.
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No device certificate or bearer token")
    return await get_current_user_with_company(credentials)
