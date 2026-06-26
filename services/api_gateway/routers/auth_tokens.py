# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Refresh-token auth endpoints (ADR-0004 decision 2).

- POST /auth/login   : authenticate and issue a short-lived access JWT + a rotating
                       refresh token. (The existing fastapi-users /auth/jwt/login remains
                       for backward compatibility and returns only the access token.)
- POST /auth/refresh : exchange a refresh token for a new access JWT and a rotated refresh
                       token (single-use rotation).
- POST /auth/logout  : revoke a refresh token, ending the session server-side.
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.exceptions import UserNotExists
from pydantic import BaseModel

from users import get_user_manager, get_jwt_strategy
from messaging import refresh_tokens

router = APIRouter(prefix="/auth", tags=["auth"])


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


async def _issue_pair(user) -> TokenPair:
    access = await get_jwt_strategy().write_token(user)
    refresh = await refresh_tokens.create_refresh_token(str(user.id))
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post("/login", response_model=TokenPair)
async def login(
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager=Depends(get_user_manager),
):
    """Authenticate and issue an access JWT plus a rotating refresh token."""
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="LOGIN_BAD_CREDENTIALS",
        )
    return await _issue_pair(user)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    body: RefreshRequest,
    user_manager=Depends(get_user_manager),
):
    """Rotate a refresh token and mint a new access JWT."""
    result = await refresh_tokens.rotate_refresh_token(body.refresh_token)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )
    user_id, new_refresh = result
    try:
        user = await user_manager.get(uuid.UUID(user_id))
    except (UserNotExists, ValueError):
        user = None
    if user is None or not user.is_active:
        # The user is gone or disabled — drop the just-issued refresh token too.
        await refresh_tokens.revoke_refresh_token(new_refresh)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )
    access = await get_jwt_strategy().write_token(user)
    return TokenPair(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(body: RefreshRequest):
    """Revoke a refresh token (end the session)."""
    await refresh_tokens.revoke_refresh_token(body.refresh_token)
