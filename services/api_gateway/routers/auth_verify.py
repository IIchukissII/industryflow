# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Session-verification endpoint for the notebook-hub SSO handoff (ADR-0014).

The notebook SSO reverse proxy issues an ``auth_request`` to this endpoint on every notebook
request. It validates the platform session exactly as the rest of the API does — there is no
second verification implementation (ADR-0000) — and returns the verified identity in ``X-IF-*``
response headers, which the proxy captures and forwards to the hub. A request without a valid
session (or a user without a tenant) is rejected by the dependency, which the proxy treats as
"deny".

This endpoint asserts identity only; it grants no data access. What a notebook kernel may reach
is minted separately by the spawner (ADR-0012).
"""
from fastapi import APIRouter, Depends, Response, status

from dependencies import get_current_user_with_company
from models.user import User

# Header names the SSO proxy reads (must match services/notebook_hub/identity.py).
HEADER_USER = "X-IF-User"
HEADER_COMPANY_ID = "X-IF-Company-Id"
HEADER_ROLE = "X-IF-Role"

router = APIRouter(tags=["Auth"])


@router.get("/auth/verify")
async def verify_session(user: User = Depends(get_current_user_with_company)) -> Response:
    """Return 204 with the verified identity in ``X-IF-*`` headers for the SSO proxy.

    Reaching this handler means the session is valid and the user has a tenant
    (``get_current_user_with_company`` raises 401/403 otherwise), so the proxy's
    ``auth_request`` allows the request only for an authenticated, tenant-bound user.
    """
    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
        headers={
            HEADER_USER: str(user.id),
            HEADER_COMPANY_ID: str(user.company_id),
            HEADER_ROLE: user.role or "",
        },
    )
