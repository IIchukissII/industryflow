# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
ML Service API Configuration
All values from environment variables - NO DEFAULT VALUES
"""
import os
import logging

logger = logging.getLogger(__name__)


class Config:
    """Configuration class - all values must be provided via environment"""
    
    # Database Configuration
    DB_HOST: str = os.getenv('DB_HOST')
    DB_PORT: str = os.getenv('DB_PORT')
    DB_NAME: str = os.getenv('DB_NAME')
    ML_SERVICE_DB_USER: str = os.getenv('ML_SERVICE_DB_USER')
    ML_SERVICE_DB_PASSWORD: str = os.getenv('ML_SERVICE_DB_PASSWORD')
    
    # MLflow Database (separate connection)
    MLFLOW_DB_NAME: str = os.getenv('MLFLOW_DB_NAME', 'mlflow')
    MLFLOW_DB_USER: str = os.getenv('MLFLOW_DB_USER')
    MLFLOW_DB_PASSWORD: str = os.getenv('MLFLOW_DB_PASSWORD')
    
    # JWT Configuration
    JWT_SECRET_KEY: str = os.getenv('JWT_SECRET_KEY')
    JWT_ALGORITHM: str = os.getenv('JWT_ALGORITHM', 'HS256')
    
    # MLflow Tracking
    MLFLOW_TRACKING_URI: str = os.getenv('MLFLOW_TRACKING_URI')
    
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
            'MLFLOW_DB_USER', 'MLFLOW_DB_PASSWORD',
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
        logger.info(f"MLflow DB: {cls.MLFLOW_DB_USER}@{cls.DB_HOST}:{cls.DB_PORT}/{cls.MLFLOW_DB_NAME}")
        logger.info(f"MLflow Tracking URI: {cls.MLFLOW_TRACKING_URI}")


config = Config
