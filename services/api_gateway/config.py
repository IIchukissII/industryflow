# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from sqlalchemy.engine import URL

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
    # Per-worker pool (the gateway's only one). Footprint: replicas x workers x (size + overflow)
    # must fit api_gateway_user's CONNECTION LIMIT; see database.py.
    API_GATEWAY_DB_POOL_SIZE: int = 5
    API_GATEWAY_DB_MAX_OVERFLOW: int = 2

    # Redis Configuration
    REDIS_HOST: str
    REDIS_PORT: int
    REDIS_DB: int

    # JWT Configuration
    JWT_SECRET_KEY: str
    # Short-lived access token; refresh token is revocable and rotated (ADR-0004 dec 2).
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    # httpOnly cookie delivery + CSRF (ADR-0004 dec 3). Every edge is HTTPS-only (ADR-0004
    # dec 1), so the cookies are Secure by default; false only for a throwaway plain-HTTP
    # development setup.
    COOKIE_SECURE: bool = True
    COOKIE_SAMESITE: str = "lax"
    # How long a replica may honour its cached view of a company's active flag (company_status.py,
    # ADR-0033 dec 3). The replica that changes the flag sees the change at once.
    COMPANY_STATUS_CACHE_SECONDS: int = 30
    # How often an open live socket re-checks its session: the account, its company, and whether
    # the session was ended (ADR-0004 dec 9). A socket outlives its session by at most this.
    WS_SESSION_RECHECK_SECONDS: int = 60
    # Password sign-in throttle (ADR-0004 dec 10, sign_in_throttle.py): per-source, or off where the
    # gateway is not told each client's own address.
    SIGN_IN_THROTTLE: str = "per-source"

    # CORS Configuration
    CORS_ORIGINS: str

    @property
    def database_url(self) -> URL:
        """Async PostgreSQL connection URL (built, not formatted, so any password is safe)."""
        return URL.create(
            "postgresql+asyncpg",
            username=self.API_GATEWAY_DB_USER,
            password=self.API_GATEWAY_DB_PASSWORD,
            host=self.DB_HOST,
            port=self.DB_PORT,
            database=self.DB_NAME,
        )

    @property
    def redis_url(self) -> str:
        """Construct Redis connection URL"""
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=True, extra="ignore")

@lru_cache()
def get_settings() -> Settings:
    """Cache settings instance"""
    return Settings()
