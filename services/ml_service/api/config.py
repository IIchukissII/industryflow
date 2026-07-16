# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
ML Service API Configuration
All values from environment variables - NO DEFAULT VALUES
"""
import os
import ssl
import logging

logger = logging.getLogger(__name__)


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
    """Configuration class - all values must be provided via environment"""
    
    # Database Configuration
    DB_HOST: str = os.getenv('DB_HOST')
    DB_PORT: str = os.getenv('DB_PORT')
    DB_NAME: str = os.getenv('DB_NAME')
    ML_SERVICE_DB_USER: str = os.getenv('ML_SERVICE_DB_USER')
    ML_SERVICE_DB_PASSWORD: str = os.getenv('ML_SERVICE_DB_PASSWORD')

    # JWT Configuration
    JWT_SECRET_KEY: str = os.getenv('JWT_SECRET_KEY')
    JWT_ALGORITHM: str = os.getenv('JWT_ALGORITHM', 'HS256')
    
    # MLflow Tracking
    MLFLOW_TRACKING_URI: str = os.getenv('MLFLOW_TRACKING_URI')

    # The platform's shared capability store (ADR-0015 dec 1), where a demand-minted upload handle's
    # binding is recorded (ADR-0030 dec 3, ADR-0015 dec 4 rev 1).
    #
    # NOT the Redis feature store ADR-0023 rev 1 removed from this service. That was a second
    # windowing substrate and it is gone for good; this is the store every capability plane already
    # shares, and the entry written here is a revocable, single-tenant handle — nothing whose loss is
    # worse than that (ADR-0015 dec 7).
    #
    # Absent = this deployment mints no upload capabilities, and the surface says so rather than
    # failing obscurely at the first request.
    CAPABILITY_REDIS_URL: str = os.getenv('CAPABILITY_REDIS_URL', '')
    # Where an uploader takes the handle. Advertised with the handle so a client does not have to
    # know the platform's internal topology to use one.
    UPLOAD_GATEWAY_URL: str = os.getenv('UPLOAD_GATEWAY_URL', '')
    
    # CORS
    CORS_ORIGINS: str = os.getenv('CORS_ORIGINS', 'http://localhost:3000')
    
    # Model Storage
    MODEL_DIR: str = os.getenv('MODEL_DIR', '/app/models')

    # Extension plugin modules to import at startup (ADR-0010). Comma-separated importable
    # module names; each registers its transforms/plugins. The core loads only what is named
    # here — never an extension directly. Empty = generic built-ins only.
    EXTENSION_MODULES: str = os.getenv('EXTENSION_MODULES', '')
    
    @classmethod
    def validate(cls):
        """Validate that all required configuration is present"""
        required = [
            'DB_HOST', 'DB_PORT', 'DB_NAME',
            'ML_SERVICE_DB_USER', 'ML_SERVICE_DB_PASSWORD',
            'JWT_SECRET_KEY', 'MLFLOW_TRACKING_URI'
        ]
        
        missing = []
        for var in required:
            if not getattr(cls, var):
                missing.append(var)
        
        if missing:
            raise RuntimeError(
                f"Missing required environment variables: {', '.join(missing)}"
            )
        
        logger.info("Configuration validated successfully")
        logger.info(f"Database: {cls.ML_SERVICE_DB_USER}@{cls.DB_HOST}:{cls.DB_PORT}/{cls.DB_NAME}")
        logger.info(f"MLflow Tracking URI: {cls.MLFLOW_TRACKING_URI}")


config = Config
