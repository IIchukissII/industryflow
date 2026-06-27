# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from pydantic_settings import BaseSettings
from functools import lru_cache

class Settings(BaseSettings):
    """Application settings loaded from environment variables"""
    
    # API Configuration
    API_TITLE: str = "IndustryFlow API"
    API_VERSION: str = "1.0.0"
    API_DESCRIPTION: str = "Real-time IoT Data Processing Platform"
    
    # Database Configuration
    DB_HOST: str
    DB_PORT: int
    DB_NAME: str
    API_GATEWAY_DB_USER: str
    API_GATEWAY_DB_PASSWORD: str
    
    # Redis Configuration
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int
    
    # JWT Configuration
    JWT_SECRET_KEY: str
    # Short-lived access token; refresh token is revocable and rotated (ADR-0004 dec 2).
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # httpOnly cookie delivery + CSRF (ADR-0004 dec 3). COOKIE_SECURE must be true in
    # production (requires HTTPS); false only for local plain-HTTP development.
    COOKIE_SECURE: bool = False
    COOKIE_SAMESITE: str = "lax"
    
    # CORS Configuration
    CORS_ORIGINS: str
    
    @property
    def database_url(self) -> str:
        """Construct async PostgreSQL connection URL"""
        return f"postgresql+asyncpg://{self.API_GATEWAY_DB_USER}:{self.API_GATEWAY_DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
    
    @property
    def redis_url(self) -> str:
        """Construct Redis connection URL"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    # Properties for asyncpg pool
    @property
    def timescaledb_host(self) -> str:
        return self.DB_HOST
    
    @property
    def timescaledb_port(self) -> int:
        return self.DB_PORT
    
    @property
    def timescaledb_user(self) -> str:
        return self.API_GATEWAY_DB_USER
    
    @property
    def timescaledb_password(self) -> str:
        return self.API_GATEWAY_DB_PASSWORD
    
    @property
    def timescaledb_database(self) -> str:
        return self.DB_NAME
    
    class Config:
        env_file = ".env"
        case_sensitive = True
        extra = "ignore"

@lru_cache()
def get_settings() -> Settings:
    """Cache settings instance"""
    return Settings()
