# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
REST API endpoints for alert rule management with schema-per-tenant routing
Supports CRUD operations and detection mode switching
"""
import json
from typing import Any, Dict, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Depends
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from dependencies import get_db_with_tenant, get_current_user_with_company, get_writer_with_company
from models.user import User
from schemas import RULE_FIELDS, AlertRuleCreate, AlertRuleUpdate, DetectionModeSwitch

router = APIRouter(prefix="/api/alert-rules", tags=["Alert Rules"])


def _bind(columns: Dict[str, Any]) -> Dict[str, Any]:
    """Bind parameters for the given columns; the JSONB config travels as JSON text."""
    params = dict(columns)
    if params.get("model_config") is not None:
        params["model_config"] = json.dumps(params["model_config"])
    return params


def _placeholder(col: str) -> str:
    return "CAST(:model_config AS JSONB)" if col == "model_config" else f":{col}"


async def _validated_merge(db: AsyncSession, rule_id: UUID, changes: Dict[str, Any]) -> None:
    """Validate the stored rule with ``changes`` applied, so a partial edit cannot leave a rule
    the detector would skip (e.g. a drift rule without its model). 404 if the rule is absent."""
    row = (await db.execute(text("SELECT * FROM alert_rules WHERE rule_id = :rule_id"),
                            {"rule_id": rule_id})).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    stored = {k: v for k, v in dict(row._mapping).items() if k in RULE_FIELDS and v is not None}
    try:
        AlertRuleCreate.model_validate({**stored, **changes})
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False, include_input=False))


async def _apply(db: AsyncSession, rule_id: UUID, changes: Dict[str, Any]):
    sets = ", ".join(f"{c} = {_placeholder(c)}" for c in changes)
    row = (await db.execute(
        text(f"UPDATE alert_rules SET {sets}, updated_at = NOW() WHERE rule_id = :rule_id RETURNING *"),
        {**_bind(changes), "rule_id": rule_id},
    )).fetchone()
    await db.commit()
    return row


@router.get("")
async def list_alert_rules(
        detection_type: Optional[str] = None,
        enabled_only: bool = True,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """List all alert rules for user's company (schema-routed)"""
    query = "SELECT * FROM alert_rules WHERE 1=1"
    params = {}
    
    if detection_type:
        query += " AND detection_type = :detection_type"
        params["detection_type"] = detection_type
    
    if enabled_only:
        query += " AND enabled = true"
    
    query += " ORDER BY created_at DESC"
    
    result = await db.execute(text(query), params)
    rows = result.fetchall()
    return [dict(row._mapping) for row in rows]


@router.get("/{rule_id}")
async def get_alert_rule(
        rule_id: UUID,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Get single alert rule by ID (schema-routed)"""
    result = await db.execute(
        text("SELECT * FROM alert_rules WHERE rule_id = :rule_id"),
        {"rule_id": rule_id}
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    
    return dict(row._mapping)


@router.post("", status_code=201)
async def create_alert_rule(
        rule: AlertRuleCreate,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_writer_with_company)
):
    """Create an alert rule in the caller's tenant schema (the schema is the isolation, ADR-0003)."""
    columns = {**rule.columns(), "created_by": current_user.email}
    cols = list(columns)
    row = (await db.execute(
        text(f"INSERT INTO alert_rules ({', '.join(cols)}) "
             f"VALUES ({', '.join(_placeholder(c) for c in cols)}) RETURNING *"),
        _bind(columns),
    )).fetchone()
    await db.commit()

    return {
        "message": "Alert rule created successfully",
        "rule": dict(row._mapping)
    }


@router.put("/{rule_id}")
async def update_alert_rule(
        rule_id: UUID,
        update: AlertRuleUpdate,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_writer_with_company)
):
    """Apply a partial edit to an alert rule; the edited rule is validated as a whole."""
    changes = update.changes()
    if not changes:
        raise HTTPException(status_code=400, detail="No fields to update")
    await _validated_merge(db, rule_id, changes)
    row = await _apply(db, rule_id, changes)

    return {
        "message": "Alert rule updated successfully",
        "rule": dict(row._mapping)
    }


@router.patch("/{rule_id}/detection-mode")
async def switch_detection_mode(
        rule_id: UUID,
        switch: DetectionModeSwitch,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_writer_with_company)
):
    """Switch a rule's detection mode; the switched rule is validated as a whole."""
    previous = (await db.execute(text("SELECT detection_type FROM alert_rules WHERE rule_id = :rule_id"),
                                 {"rule_id": rule_id})).fetchone()
    changes = switch.changes()
    await _validated_merge(db, rule_id, changes)
    row = await _apply(db, rule_id, changes)

    return {
        "message": "Detection mode switched successfully",
        "previous_mode": previous.detection_type if previous else None,
        "new_mode": changes["detection_type"],
        "rule": dict(row._mapping)
    }


@router.delete("/{rule_id}")
async def delete_alert_rule(
        rule_id: UUID,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_writer_with_company)
):
    """Delete alert rule (schema-routed)"""
    result = await db.execute(
        text("DELETE FROM alert_rules WHERE rule_id = :rule_id RETURNING *"),
        {"rule_id": rule_id}
    )
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    
    await db.commit()
    
    return {
        "message": "Alert rule deleted successfully",
        "deleted_rule": dict(row._mapping)
    }
