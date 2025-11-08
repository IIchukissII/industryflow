"""
Configuration for Alert Service API
All values from environment variables - NO DEFAULTS
"""
import os
from typing import Optional


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
    Convert company_id (UUID) to schema name
    Example: 550e8400-e29b-41d4-a716-446655440000 
          -> tenant_550e8400_e29b_41d4_a716_446655440000
    """
    clean_id = company_id.replace('-', '_')
    return f"tenant_{clean_id}"
