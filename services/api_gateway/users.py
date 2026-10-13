# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# src/api/users.py
import uuid
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users import FastAPIUsers


from models.user import User
from database import get_user_db
from config import get_settings
from messaging import refresh_tokens

from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.jwt import generate_jwt

# Cookie name for the httpOnly access-token cookie (ADR-0004 dec 3).
ACCESS_COOKIE = "if_access"

settings = get_settings()

# Secret key for token generation (should be in your .env)
SECRET = settings.JWT_SECRET_KEY


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """
    Custom User Manager for handling user-related events
    """
    reset_password_token_secret = SECRET
    verification_token_secret = SECRET

    async def on_after_register(self, user: User, request: Optional[Request] = None):
        """Called after a user successfully registers"""
        print(f"User {user.id} has registered. Email: {user.email}, Company: {user.company_id}, Role: {user.role}")

    async def on_after_forgot_password(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        """Called after a user requests password reset.

        The reset token is a credential and is never logged (ADR-0004 dec 7); it is delivered
        to the user out of band. Log only the event.
        """
        print(f"User {user.id} requested a password reset")

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        """Called after a user requests verification. The verification token is a credential
        and is never logged (ADR-0004 dec 7)."""
        print(f"User {user.id} requested verification")

    # A session must not outlive the credential or the account it was opened with (ADR-0004
    # dec 2): a password change, a password reset, deactivation and deletion end every one of
    # the user's sessions (refresh tokens). Access tokens are short-lived, and the per-request
    # user load refuses an inactive or deleted user, so nothing else stays usable.
    async def on_after_update(self, user: User, update_dict: dict, request: Optional[Request] = None):
        if "password" in update_dict or update_dict.get("is_active") is False:
            await refresh_tokens.end_all_sessions(str(user.id))

    async def on_after_reset_password(self, user: User, request: Optional[Request] = None):
        await refresh_tokens.end_all_sessions(str(user.id))

    async def on_after_delete(self, user: User, request: Optional[Request] = None):
        await refresh_tokens.end_all_sessions(str(user.id))


async def get_user_manager(user_db=Depends(get_user_db)):
    """Dependency to get User Manager instance"""
    yield UserManager(user_db)

bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")


class CompanyClaimJWTStrategy(JWTStrategy):
    """
    JWT strategy that embeds company_id and role as claims, so downstream services resolve
    the tenant directly from the token instead of scanning every tenant schema for the user
    (ADR-0003 decision 2; closes review finding X2).
    """

    async def write_token(self, user) -> str:
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "company_id": str(user.company_id) if user.company_id else None,
            "role": getattr(user, "role", None),
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )


def get_jwt_strategy() -> JWTStrategy:
    """
    The short-lived access-token strategy (JWT_ACCESS_TOKEN_EXPIRE_MINUTES, default 30). The
    session's longevity and revocation live in the refresh token (ADR-0004 dec 2,
    routers/auth_tokens.py).
    """
    return CompanyClaimJWTStrategy(
        secret=SECRET,
        lifetime_seconds=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )

# Authentication backend combining transport + strategy
auth_backend = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)

# Cookie backend: the same JWT access token, delivered/read as an httpOnly cookie so it is
# not exposed to page JavaScript (ADR-0004 dec 3). current_user accepts either transport.
cookie_transport = CookieTransport(
    cookie_name=ACCESS_COOKIE,
    cookie_max_age=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    cookie_secure=settings.COOKIE_SECURE,
    cookie_httponly=True,
    cookie_samesite=settings.COOKIE_SAMESITE,
)
cookie_auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)

# FastAPI Users instance - provides all auth routes and dependencies
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend, cookie_auth_backend],
)

# Dependency to get current active user
current_active_user = fastapi_users.current_user(active=True)