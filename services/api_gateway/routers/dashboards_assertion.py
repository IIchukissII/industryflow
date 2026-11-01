# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The dashboards' sign-on assertion (ADR-0016 dec 7).

The edge issues an ``auth_request`` here on every request to the dashboards it serves, and forwards
the assertion this returns to them. Only a platform superuser gets one (ADR-0043 dec 1): the session
dependency answers 401 without a session and 403 for anyone else, and the edge refuses the request
on either. The dashboards verify the signature, issuer, audience and expiry with the public half of
the key, so an identity header that only network position vouches for signs no one in.

The path is under ``/internal/``, which the edge never routes: the assertion goes from here to the
edge to the dashboards and never to the browser.
"""
from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response, status
from jose import jwt

from dependencies import require_superuser
from models.user import User

logger = logging.getLogger(__name__)

PATH = "/internal/dashboards/assertion"
# The header the edge lifts from this response and the dashboards read the assertion from; the
# dashboards' configuration (docker-compose.yml, GF_AUTH_JWT_HEADER_NAME) names the same one.
ASSERTION_HEADER = "X-IF-Dashboards-Assertion"
ISSUER = "industryflow-api-gateway"
AUDIENCE = "industryflow-dashboards"
# Minted per request, so it only needs to outlive the hop from the edge to the dashboards.
LIFETIME_SECONDS = 60
# Reading and exploring every service's metrics and logs; never the dashboards' administrator.
ROLE = "Editor"
ALGORITHM = "ES256"

router = APIRouter(tags=["Auth"])

_cache: dict[str, tuple[float, str]] = {}


def _signing_key() -> str | None:
    """The PEM private key, re-read when the file changes so a replaced key needs no restart."""
    path = os.environ.get("DASHBOARDS_SIGNING_KEY_FILE", "")
    if not path:
        return None
    try:
        mtime = Path(path).stat().st_mtime
        cached = _cache.get(path)
        if cached and cached[0] == mtime:
            return cached[1]
        key = Path(path).read_text()
    except OSError:
        logger.exception("dashboards signing key unreadable at %s", path)
        return None
    _cache[path] = (mtime, key)
    return key


@router.get(PATH, include_in_schema=False)
async def dashboards_assertion(user: User = Depends(require_superuser)) -> Response:
    key = _signing_key()
    if key is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Dashboard sign-on is not configured")
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": user.email,
        "email": user.email,
        "role": ROLE,
        "iat": now,
        "nbf": now,
        "exp": now + LIFETIME_SECONDS,
    }
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={ASSERTION_HEADER: jwt.encode(claims, key, algorithm=ALGORITHM),
                 "Cache-Control": "no-store"},
    )
