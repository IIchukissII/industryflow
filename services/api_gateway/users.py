# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# src/api/users.py
import uuid
from typing import Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users import FastAPIUsers

from schemas import UserRead, UserCreate, UserUpdate

from models.user import User
from database import get_user_db
from config import get_settings

from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.jwt import generate_jwt

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
        """Called after a user requests password reset"""
        print(f"User {user.id} has forgot their password. Reset token: {token}")

    async def on_after_request_verify(
        self, user: User, token: str, request: Optional[Request] = None
    ):
        """Called after a user requests verification"""
        print(f"Verification requested for user {user.id}. Verification token: {token}")


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
    JWT strategy with a 1-week token lifetime. (ADR-0004 will shorten this and add
    refresh/revocation in a later step of the auth-model refactor.)
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

# FastAPI Users instance - provides all auth routes and dependencies
fastapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [auth_backend],
)

# Dependency to get current active user
current_active_user = fastapi_users.current_user(active=True)