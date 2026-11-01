# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# src/api/users.py
import logging
import time
import uuid
from typing import Optional

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi_users import BaseUserManager, UUIDIDMixin
from fastapi_users import FastAPIUsers
from fastapi.security import OAuth2PasswordRequestForm


from models.user import User
from database import get_user_db
from config import get_settings
from messaging import refresh_tokens
import company_status

from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.jwt import decode_jwt, generate_jwt

logger = logging.getLogger(__name__)

# Cookie name for the httpOnly access-token cookie (ADR-0004 dec 3).
ACCESS_COOKIE = "if_access"

settings = get_settings()

# Secret key for token generation (should be in your .env)
SECRET = settings.JWT_SECRET_KEY

# Access tokens are signed with HMAC-SHA256 under SECRET. A constant, not a setting: a verifier
# whose accepted algorithm is configurable invites algorithm confusion.
JWT_ALGORITHM = "HS256"


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

    async def authenticate(self, credentials: OAuth2PasswordRequestForm) -> Optional[User]:
        """A user of a suspended company cannot sign in (ADR-0033 dec 3); refused like a bad
        password, so the answer does not reveal the company's state."""
        user = await super().authenticate(credentials)
        if user is not None and user.company_id and not await company_status.is_company_active(user.company_id):
            return None
        return user

    # A session must not outlive the credential or the account it was opened with (ADR-0004
    # dec 2): a password change, a password reset, deactivation and deletion end every one of
    # the user's sessions (refresh tokens). Access tokens are short-lived, and the per-request
    # user load refuses an inactive or deleted user, so nothing else stays usable.
    async def on_after_update(self, user: User, update_dict: dict, request: Optional[Request] = None):
        # A move to another company too: the access tokens name the tenant they were issued for.
        if "password" in update_dict or "company_id" in update_dict or update_dict.get("is_active") is False:
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

    async def write_token(self, user, session_id: Optional[str] = None) -> str:
        """``session_id`` names the refresh-token session the token is minted in, and ``iat``
        when: together they let every request, and a live socket, refuse a token whose session has
        ended (ADR-0004 dec 2, 9; messaging/refresh_tokens.session_ended). A token minted without
        a refresh session (the fastapi-users login) is its own session, so its logout can end it."""
        session_id = session_id or refresh_tokens.new_session_id()
        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "iat": int(time.time()),
            "company_id": str(user.company_id) if user.company_id else None,
            "role": getattr(user, "role", None),
        }
        data["sid"] = session_id
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )

    async def read_token(self, token, user_manager):
        """The token's user, unless that user's company is suspended (ADR-0033 dec 3, through the
        cached company flag in company_status.py) or the session the token was minted in has ended
        (ADR-0004 dec 2): logout, a password change or reset, deactivation and deletion refuse the
        access tokens issued before them on the next request, not when they expire.

        The session store is read on every request; when it cannot be read the request fails with
        503 rather than being admitted, since an unknown answer is not a yes.
        """
        user = await super().read_token(token, user_manager)
        if user is None:
            return None
        if user.company_id and not await company_status.is_company_active(user.company_id):
            return None
        claims = self.claims(token)
        if claims is None:
            return None
        try:
            ended = await refresh_tokens.session_ended(str(user.id), claims.get("sid"), claims.get("iat"))
        except Exception as exc:  # noqa: BLE001 - any store failure leaves the session unknown
            logger.error("session store unreadable, refusing the request: %s", type(exc).__name__)
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Session store not available")
        return None if ended else user

    def claims(self, token: str) -> Optional[dict]:
        """The verified claims of an unexpired access token, else None."""
        try:
            return decode_jwt(token, self.decode_key, self.token_audience, algorithms=[self.algorithm])
        except jwt.PyJWTError:
            return None

    async def destroy_token(self, token: str, user) -> None:
        """The fastapi-users logout ends the token's session and the user's notebook capabilities,
        as /auth/logout does (ADR-0004 dec 2)."""
        from capability_auth import revoke_user_capabilities
        from messaging.redis_client import redis_client

        claims = self.claims(token) or {}
        if claims.get("sid"):
            await refresh_tokens.end_session(claims["sid"])
        await revoke_user_capabilities(redis_client.redis, str(user.id))


def get_jwt_strategy() -> JWTStrategy:
    """
    The short-lived access-token strategy (JWT_ACCESS_TOKEN_EXPIRE_MINUTES, default 30). The
    session's longevity and revocation live in the refresh token (ADR-0004 dec 2,
    routers/auth_tokens.py).
    """
    return CompanyClaimJWTStrategy(
        secret=SECRET,
        lifetime_seconds=settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        algorithm=JWT_ALGORITHM,
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