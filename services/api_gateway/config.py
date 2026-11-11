# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import re
from typing import Optional

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from sqlalchemy.engine import URL

# The gateway's CORS policy sends credentials (the session cookie), so its origin allowlist may never
# hold the wildcard (ADR-0004 dec 6).
CORS_ALLOW_CREDENTIALS = True
# An origin: scheme, host and an optional port, nothing after.
_ORIGIN = re.compile(r"https?://[A-Za-z0-9.-]+(?::[0-9]{1,5})?")


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
    # The first-run superuser (ADR-0004 dec 11, first_run.py), created only on a platform with no
    # account. Unset:
    # the published defaults, and the account must change its password before anything else.
    PLATFORM_ADMIN_EMAIL: Optional[str] = None
    PLATFORM_ADMIN_PASSWORD: Optional[str] = None
    PLATFORM_ADMIN_TENANT: Optional[str] = None

    # CORS Configuration: an explicit origin allowlist, comma-separated (ADR-0004 dec 6)
    CORS_ORIGINS: str

    @field_validator("CORS_ORIGINS")
    @classmethod
    def _no_wildcard_origin_with_credentials(cls, value: str) -> str:
        """Refuse ``*`` in the allowlist while credentials are allowed, at startup (ADR-0004 dec 6)."""
        if CORS_ALLOW_CREDENTIALS and "*" in cors_origin_list(value):
            raise ValueError(
                "CORS_ORIGINS must list explicit origins: '*' is refused because the gateway "
                "allows credentials (ADR-0004 dec 6)"
            )
        return value

    # The notebook origin (ADR-0048), as scheme://host[:port]; unset where notebooks are not deployed,
    # and then no ticket is minted. It is in no allowlist (dec 2).
    NOTEBOOK_ORIGIN: Optional[str] = None

    @field_validator("NOTEBOOK_ORIGIN")
    @classmethod
    def _a_notebook_origin_of_its_own(cls, value: Optional[str], info) -> Optional[str]:
        """An origin and nothing more, never one the allowlist admits (ADR-0048 dec 1, 2)."""
        if value is None or not value.strip():
            return None
        value = value.strip().rstrip("/")
        if not _ORIGIN.fullmatch(value):
            raise ValueError("NOTEBOOK_ORIGIN must be an origin, https://host[:port] (ADR-0048)")
        if value in {o.rstrip("/") for o in cors_origin_list(info.data.get("CORS_ORIGINS") or "")}:
            raise ValueError("NOTEBOOK_ORIGIN must not be in CORS_ORIGINS (ADR-0048 dec 2)")
        return value

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

def cors_origin_list(value: str) -> list[str]:
    """The origins a comma-separated ``CORS_ORIGINS`` value names."""
    return [o.strip() for o in value.split(",") if o.strip()]


@lru_cache()
def get_settings() -> Settings:
    """Cache settings instance"""
    return Settings()
