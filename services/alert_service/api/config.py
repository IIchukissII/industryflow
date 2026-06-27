# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Configuration for Alert Service API
All values from environment variables - NO DEFAULTS
"""
import os
import ssl
import uuid


def db_ssl_context():
    """SSLContext for DB connections per DB_SSLMODE (ADR-0017). Default verify-full: encrypt +
    verify the server cert against DB_SSLROOTCERT (the internal CA) with hostname checking."""
    mode = (os.getenv("DB_SSLMODE") or "verify-full").lower()
    if mode in ("disable", "allow", ""):
        return None
    ca = os.getenv("DB_SSLROOTCERT") or None
    if ca and not os.path.exists(ca):
        ca = None
    ctx = ssl.create_default_context(cafile=ca)
    if mode in ("require", "prefer"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    elif mode == "verify-ca":
        ctx.check_hostname = False
    return ctx


class Config:
    """Alert Service API configuration from environment"""
    
    # Database configuration
    DB_HOST: str = os.getenv('DB_HOST')
    DB_PORT: int = int(os.getenv('DB_PORT'))
    DB_NAME: str = os.getenv('DB_NAME')
    ALERT_SERVICE_DB_USER: str = os.getenv('ALERT_SERVICE_DB_USER')
    ALERT_SERVICE_DB_PASSWORD: str = os.getenv('ALERT_SERVICE_DB_PASSWORD')
    
    # Database pool configuration
    DB_MIN_SIZE: int = int(os.getenv('ALERT_API_DB_MIN_SIZE', '5'))
    DB_MAX_SIZE: int = int(os.getenv('ALERT_API_DB_MAX_SIZE', '20'))
    
    # JWT configuration
    JWT_SECRET_KEY: str = os.getenv('JWT_SECRET_KEY')
    JWT_ALGORITHM: str = os.getenv('JWT_ALGORITHM', 'HS256')
    
    # API configuration
    API_PORT: int = int(os.getenv('ALERT_SERVICE_PORT', '8001'))
    CORS_ORIGINS: str = os.getenv('CORS_ORIGINS', 'http://localhost:3000')
    
    # Logging
    LOG_LEVEL: str = os.getenv('LOG_LEVEL', 'INFO')
    
    @classmethod
    def validate(cls):
        """Validate that all required configuration is present"""
        required = [
            'DB_HOST', 'DB_PORT', 'DB_NAME',
            'ALERT_SERVICE_DB_USER', 'ALERT_SERVICE_DB_PASSWORD',
            'JWT_SECRET_KEY'
        ]
        
        missing = []
        for var in required:
            if not getattr(cls, var):
                missing.append(var)
        
        if missing:
            raise ValueError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        
        print(f"✅ Configuration validated: {cls.ALERT_SERVICE_DB_USER}@{cls.DB_HOST}")


def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Convert a company_id (UUID) to its tenant schema name.

    company_id is validated as a UUID first, so the result is safe to interpolate into a
    SET search_path statement; a non-UUID raises ValueError instead of reaching SQL
    (defends against injection — see ADR-0003).
    Example: 550e8400-e29b-41d4-a716-446655440000
          -> tenant_550e8400_e29b_41d4_a716_446655440000
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"
