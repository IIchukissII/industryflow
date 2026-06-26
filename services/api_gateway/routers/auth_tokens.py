# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Refresh-token auth endpoints (ADR-0004 decisions 2 & 3).

- POST /auth/login   : authenticate and issue a short-lived access JWT + a rotating
                       refresh token, delivered both as a JSON body (for API clients) and
                       as httpOnly cookies (for browsers), plus a JS-readable CSRF cookie.
- POST /auth/refresh : rotate the refresh token (from body or cookie) and mint a new access
                       JWT; refreshes the cookies.
- POST /auth/logout  : revoke the refresh token and clear the cookies.

The existing fastapi-users /auth/jwt/login remains for backward compatibility.
"""
import secrets
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from fastapi_users.exceptions import UserNotExists
from pydantic import BaseModel

from config import get_settings
from users import get_user_manager, get_jwt_strategy, ACCESS_COOKIE
from messaging import refresh_tokens

router = APIRouter(prefix="/auth", tags=["auth"])

REFRESH_COOKIE = "if_refresh"
CSRF_COOKIE = "if_csrf"


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: Optional[str] = None


def _set_auth_cookies(response: Response, access: str, refresh: str) -> None:
    s = get_settings()
    access_max = s.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60
    refresh_max = s.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 24 * 3600
    # Access token: httpOnly (invisible to JS — XSS cannot read it), sent to all paths.
    response.set_cookie(ACCESS_COOKIE, access, max_age=access_max, httponly=True,
                        secure=s.COOKIE_SECURE, samesite=s.COOKIE_SAMESITE, path="/")
    # Refresh token: httpOnly and scoped to /auth so it is only sent to the auth endpoints.
    response.set_cookie(REFRESH_COOKIE, refresh, max_age=refresh_max, httponly=True,
                        secure=s.COOKIE_SECURE, samesite=s.COOKIE_SAMESITE, path="/auth")
    # CSRF token: readable by JS so the SPA can echo it in the X-CSRF-Token header
    # (double-submit; the cookie is auto-sent, the header is not, so cross-site cannot forge it).
    response.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(32), max_age=refresh_max,
                        httponly=False, secure=s.COOKIE_SECURE, samesite=s.COOKIE_SAMESITE, path="/")


def _clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/auth")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.post("/login", response_model=TokenPair)
async def login(
    response: Response,
    credentials: OAuth2PasswordRequestForm = Depends(),
    user_manager=Depends(get_user_manager),
):
    """Authenticate and issue an access JWT plus a rotating refresh token (+ cookies)."""
    user = await user_manager.authenticate(credentials)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="LOGIN_BAD_CREDENTIALS")
    access = await get_jwt_strategy().write_token(user)
    refresh = await refresh_tokens.create_refresh_token(str(user.id))
    _set_auth_cookies(response, access, refresh)
    return TokenPair(access_token=access, refresh_token=refresh)


@router.post("/refresh", response_model=TokenPair)
async def refresh(
    request: Request,
    response: Response,
    body: Optional[RefreshRequest] = None,
    user_manager=Depends(get_user_manager),
):
    """Rotate the refresh token (from body or cookie) and mint a new access JWT."""
    token = (body.refresh_token if body and body.refresh_token else None) or request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token")
    result = await refresh_tokens.rotate_refresh_token(token)
    if result is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired refresh token")
    user_id, new_refresh = result
    try:
        user = await user_manager.get(uuid.UUID(user_id))
    except (UserNotExists, ValueError):
        user = None
    if user is None or not user.is_active:
        await refresh_tokens.revoke_refresh_token(new_refresh)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    access = await get_jwt_strategy().write_token(user)
    _set_auth_cookies(response, access, new_refresh)
    return TokenPair(access_token=access, refresh_token=new_refresh)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, body: Optional[RefreshRequest] = None):
    """Revoke the refresh token (from body or cookie) and clear the auth cookies."""
    token = (body.refresh_token if body and body.refresh_token else None) or request.cookies.get(REFRESH_COOKIE)
    if token:
        await refresh_tokens.revoke_refresh_token(token)
    _clear_auth_cookies(response)
