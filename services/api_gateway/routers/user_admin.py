# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
User self-service and tenant-scoped user administration (ADR-0003 dec 1, ADR-0004 dec 4-5).

There is no public registration. A user is created by an admin, into the admin's own company; only
a superuser chooses the company or grants superuser. A user may change their own email and password,
never their tenant or role. An admin acts only on users of their own company; a user of another
company answers 404, so its existence is not confirmed.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi_users import exceptions
from sqlalchemy import text

from database import AsyncSessionLocal
from dependencies import require_role
from models.user import User
from schemas import AdminUserCreate, AdminUserUpdate, UserCreate, UserRead, UserSelfUpdate
from users import current_active_user, get_user_manager

router = APIRouter(tags=["users"])


def _invalid_password(exc, code: str) -> HTTPException:
    """A password the user manager refuses is the caller's mistake (400), in fastapi-users' own shape,
    which the SPA shows as the reason (ADR-0004 dec 11: the default cannot be kept)."""
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"code": code, "reason": exc.reason})


@router.get("/users/me", response_model=UserRead)
async def read_me(user: User = Depends(current_active_user)):
    return user


@router.patch("/users/me", response_model=UserRead)
async def update_me(
    body: UserSelfUpdate,
    request: Request,
    user: User = Depends(current_active_user),
    manager=Depends(get_user_manager),
):
    try:
        return await manager.update(body, user, safe=True, request=request)
    except exceptions.InvalidPasswordException as e:
        raise _invalid_password(e, "UPDATE_USER_INVALID_PASSWORD")
    except exceptions.UserAlreadyExists:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")


async def _target_in_scope(user_id: UUID, admin: User, manager) -> User:
    """The target user, if it is in the admin's company (any company for a superuser); else 404."""
    try:
        target = await manager.get(user_id)
    except exceptions.UserNotExists:
        target = None
    if target is None or (not admin.is_superuser and target.company_id != admin.company_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    # A tenant admin who could reset a superuser's password would become one.
    if target.is_superuser and not admin.is_superuser:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superuser privileges required")
    return target


def _refuse_superuser_only(admin: User, fields: dict) -> None:
    """Moving a user between companies and granting superuser are superuser-only."""
    if not admin.is_superuser and ("company_id" in fields or "is_superuser" in fields):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superuser privileges required")


@router.get("/api/users")
async def list_users(admin: User = Depends(require_role("admin"))):
    """Users of the admin's company (every company for a superuser)."""
    columns = 'SELECT id, email, company_id, role, is_active, is_superuser, is_verified FROM public."user"'
    async with AsyncSessionLocal() as session:
        if admin.is_superuser:
            result = await session.execute(text(f"{columns} ORDER BY email"))
        else:
            result = await session.execute(
                text(f"{columns} WHERE company_id = :company_id ORDER BY email"),
                {"company_id": admin.company_id},
            )
        return [dict(row) for row in result.mappings().all()]


@router.post("/api/users", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: AdminUserCreate,
    request: Request,
    admin: User = Depends(require_role("admin")),
    manager=Depends(get_user_manager),
):
    fields = body.model_dump(exclude_unset=True)
    # Values that change nothing (the form sends them back as-is) are not privilege requests.
    if fields.get("company_id") == admin.company_id:
        fields.pop("company_id")
    if fields.get("is_superuser") is False:
        fields.pop("is_superuser")
    _refuse_superuser_only(admin, fields)
    create = UserCreate(
        email=body.email,
        password=body.password,
        role=body.role,
        company_id=fields.get("company_id") or admin.company_id,
        is_active=body.is_active,
        is_superuser=fields.get("is_superuser", False),
        is_verified=False,
    )
    try:
        return await manager.create(create, safe=False, request=request)
    except exceptions.InvalidPasswordException as e:
        raise _invalid_password(e, "REGISTER_INVALID_PASSWORD")
    except exceptions.UserAlreadyExists:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")


@router.patch("/api/users/{user_id}", response_model=UserRead)
async def update_user(
    user_id: UUID,
    body: AdminUserUpdate,
    request: Request,
    admin: User = Depends(require_role("admin")),
    manager=Depends(get_user_manager),
):
    target = await _target_in_scope(user_id, admin, manager)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("company_id") == target.company_id:
        fields.pop("company_id")
    if fields.get("is_superuser") == target.is_superuser:
        fields.pop("is_superuser")
    _refuse_superuser_only(admin, fields)
    try:
        return await manager.update(AdminUserUpdate(**fields), target, safe=False, request=request)
    except exceptions.InvalidPasswordException as e:
        raise _invalid_password(e, "UPDATE_USER_INVALID_PASSWORD")
    except exceptions.UserAlreadyExists:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already in use")


@router.delete("/api/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: UUID,
    request: Request,
    admin: User = Depends(require_role("admin")),
    manager=Depends(get_user_manager),
):
    target = await _target_in_scope(user_id, admin, manager)
    if target.id == admin.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot delete yourself")
    await manager.delete(target, request=request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
