"""
Alert Rules Router
Schema-per-tenant architecture with JWT-based company_id
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from typing import List, Optional
import asyncpg
import sys
from pathlib import Path

# Add parent directory to path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

from models import AlertRule, AlertRuleCreate, AlertRuleUpdate, DetectionModeSwitch
from repository import AlertRuleRepository
from auth import get_company_id

router = APIRouter(prefix="/api/alert-rules", tags=["Alert Rules"])


async def get_db_pool(request: Request):
    """Get database pool from app state"""
    db_pool = request.app.state.db_pool
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_pool


@router.get("", response_model=List[AlertRule])
async def list_alert_rules(
    company_id: str = Depends(get_company_id),
    detection_type: Optional[str] = None,
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """
    List alert rules for authenticated user's company.
    Automatically filtered by company_id from JWT token.
    """
    repo = AlertRuleRepository(db_pool)
    rules = await repo.get_all_active_rules(company_id, detection_type)
    return rules


@router.get("/{rule_id}", response_model=AlertRule)
async def get_alert_rule(
    rule_id: str,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Get a specific alert rule by ID"""
    repo = AlertRuleRepository(db_pool)
    rule = await repo.get_rule_by_id(rule_id, company_id)

    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    return rule


@router.post("", response_model=AlertRule, status_code=201)
async def create_alert_rule(
    rule: AlertRuleCreate,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Create a new alert rule for authenticated user's company"""
    repo = AlertRuleRepository(db_pool)

    try:
        new_rule = await repo.create_rule(rule, company_id)
        return new_rule
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to create rule: {str(e)}")


@router.put("/{rule_id}", response_model=AlertRule)
async def update_alert_rule(
    rule_id: str,
    rule: AlertRuleUpdate,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Update an existing alert rule"""
    repo = AlertRuleRepository(db_pool)

    existing_rule = await repo.get_rule_by_id(rule_id, company_id)
    if not existing_rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    try:
        updated_rule = await repo.update_rule(rule_id, rule, company_id)
        return updated_rule
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to update rule: {str(e)}")


@router.delete("/{rule_id}", status_code=204)
async def delete_alert_rule(
    rule_id: str,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Delete an alert rule"""
    repo = AlertRuleRepository(db_pool)

    existing_rule = await repo.get_rule_by_id(rule_id, company_id)
    if not existing_rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    try:
        await repo.delete_rule(rule_id, company_id)
        return None
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to delete rule: {str(e)}")


@router.patch("/{rule_id}/detection-mode", response_model=dict)
async def switch_detection_mode(
    rule_id: str,
    mode_switch: DetectionModeSwitch,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Switch detection mode for a rule"""
    repo = AlertRuleRepository(db_pool)

    existing_rule = await repo.get_rule_by_id(rule_id, company_id)
    if not existing_rule:
        raise HTTPException(status_code=404, detail="Rule not found")

    try:
        result = await repo.switch_detection_mode(rule_id, mode_switch, company_id)
        return {"success": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to switch mode: {str(e)}")
