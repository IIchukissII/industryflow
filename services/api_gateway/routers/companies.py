# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Companies management endpoints for admin panel
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from uuid import UUID
import logging

from dependencies import get_db_with_tenant, require_role
from models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/companies", tags=["companies"])


class CompanyCreate(BaseModel):
    company_name: str
    is_active: bool = True


class CompanyUpdate(BaseModel):
    company_name: Optional[str] = None
    is_active: Optional[bool] = None


class CompanyResponse(BaseModel):
    company_id: UUID
    company_name: str
    is_active: bool
    created_at: datetime


def _require_superuser(current_user: User) -> None:
    """Provisioning a tenant (create/delete a company) requires a superuser (ADR-0004)."""
    if not current_user.is_superuser:
        raise HTTPException(status_code=403, detail="Superuser privileges required")


def _assert_own_company(current_user: User, company_id: UUID) -> None:
    """
    A non-superuser admin may only act on their own company (ADR-0004 decision 5).
    Returns 404 rather than 403 so other tenants' company IDs are not confirmed.
    """
    if not current_user.is_superuser and str(current_user.company_id) != str(company_id):
        raise HTTPException(status_code=404, detail="Company not found")


@router.get("", response_model=List[CompanyResponse])
async def list_companies(
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """List companies. Superusers see all; a per-tenant admin sees only their own (ADR-0004)."""
    if current_user.is_superuser:
        result = await db.execute(text("""
            SELECT company_id, company_name, is_active, created_at
            FROM public.companies
            ORDER BY company_name
        """))
    else:
        result = await db.execute(text("""
            SELECT company_id, company_name, is_active, created_at
            FROM public.companies
            WHERE company_id = :company_id
            ORDER BY company_name
        """), {"company_id": current_user.company_id})

    rows = result.fetchall()
    return [
        {
            "company_id": row.company_id,
            "company_name": row.company_name,
            "is_active": row.is_active,
            "created_at": row.created_at
        }
        for row in rows
    ]


@router.get("/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """Get a single company by ID (own company only, unless superuser)."""
    _assert_own_company(current_user, company_id)
    result = await db.execute(
        text("""
            SELECT company_id, company_name, is_active, created_at
            FROM public.companies
            WHERE company_id = :company_id
        """),
        {"company_id": company_id}
    )
    
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
    
    return {
        "company_id": row.company_id,
        "company_name": row.company_name,
        "is_active": row.is_active,
        "created_at": row.created_at
    }


@router.post("", response_model=CompanyResponse)
async def create_company(
    company: CompanyCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """Create a new company/tenant (superuser only — this is a provisioning action)."""
    _require_superuser(current_user)
    # Check if company name already exists
    result = await db.execute(
        text("SELECT company_id FROM public.companies WHERE company_name = :name"),
        {"name": company.company_name}
    )
    
    if result.fetchone():
        raise HTTPException(
            status_code=400,
            detail=f"Company '{company.company_name}' already exists"
        )
    
    # Create company
    result = await db.execute(
        text("""
            INSERT INTO public.companies (company_name, is_active)
            VALUES (:name, :is_active)
            RETURNING company_id, company_name, is_active, created_at
        """),
        {"name": company.company_name, "is_active": company.is_active}
    )
    
    row = result.fetchone()
    await db.commit()
    
    logger.info(f"Created company: {company.company_name}")
    return {
        "company_id": row.company_id,
        "company_name": row.company_name,
        "is_active": row.is_active,
        "created_at": row.created_at
    }


@router.put("/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    company: CompanyUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """Update a company (own company only, unless superuser)."""
    _assert_own_company(current_user, company_id)
    # Build update query
    updates = []
    params = {"company_id": company_id}
    
    if company.company_name is not None:
        updates.append("company_name = :company_name")
        params["company_name"] = company.company_name
    
    if company.is_active is not None:
        updates.append("is_active = :is_active")
        params["is_active"] = company.is_active
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    query = f"""
        UPDATE public.companies
        SET {', '.join(updates)}
        WHERE company_id = :company_id
        RETURNING company_id, company_name, is_active, created_at
    """
    
    result = await db.execute(text(query), params)
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Company not found")
    
    await db.commit()
    logger.info(f"Updated company: {company_id}")
    
    return {
        "company_id": row.company_id,
        "company_name": row.company_name,
        "is_active": row.is_active,
        "created_at": row.created_at
    }


@router.delete("/{company_id}")
async def delete_company(
    company_id: UUID,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(require_role("admin"))
):
    """Delete a company/tenant (superuser only — this is a provisioning action)."""
    _require_superuser(current_user)
    # Check if company has users
    result = await db.execute(
        text('SELECT COUNT(*) FROM public."user" WHERE company_id = :company_id'),
        {"company_id": company_id}
    )
    user_count = result.scalar()
    
    if user_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot delete company with {user_count} users. Delete users first."
        )
    
    # Delete company
    result = await db.execute(
        text("DELETE FROM public.companies WHERE company_id = :company_id"),
        {"company_id": company_id}
    )
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Company not found")
    
    await db.commit()
    logger.info(f"Deleted company: {company_id}")
    return {"message": "Company deleted successfully"}
