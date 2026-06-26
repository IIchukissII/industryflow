# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
REST API endpoints for alert rule management with schema-per-tenant routing
Supports CRUD operations and detection mode switching
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Optional
from uuid import UUID
from dependencies import get_db_with_tenant, get_current_user_with_company
from models.user import User

router = APIRouter(prefix="/api/alert-rules", tags=["Alert Rules"])


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
        rule_data: dict,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Create new alert rule for user's company (schema ensures tenant isolation)"""
    # Validate required fields
    if not rule_data.get('name'):
        raise HTTPException(status_code=400, detail="name is required")
    
    # Insert into tenant schema (company_id not needed)
    params = {
        "name": rule_data.get('name'),
        "description": rule_data.get('description'),
        "sensor_pattern": rule_data.get('sensor_pattern'),
        "sensor_id": rule_data.get('sensor_id'),
        "equipment_id": rule_data.get('equipment_id'),
        "site_id": rule_data.get('site_id'),
        "detection_type": rule_data.get('detection_type', 'threshold'),
        "condition": rule_data.get('condition'),
        "threshold": rule_data.get('threshold'),
        "threshold_min": rule_data.get('threshold_min'),
        "threshold_max": rule_data.get('threshold_max'),
        "model_id": rule_data.get('model_id'),
        "anomaly_threshold": rule_data.get('anomaly_threshold', 0.7),
        "severity": rule_data.get('severity', 'medium'),
        "priority": rule_data.get('priority', 3),
        "enabled": rule_data.get('enabled', True)
    }
    
    result = await db.execute(text("""
        INSERT INTO alert_rules (
            name, description, sensor_pattern, sensor_id, equipment_id,
            site_id, detection_type, condition, threshold, threshold_min, threshold_max,
            model_id, anomaly_threshold, severity, priority, enabled
        ) VALUES (
            :name, :description, :sensor_pattern, :sensor_id, :equipment_id,
            :site_id, :detection_type, :condition, :threshold, :threshold_min, :threshold_max,
            :model_id, :anomaly_threshold, :severity, :priority, :enabled
        )
        RETURNING *
    """), params)
    
    row = result.fetchone()
    await db.commit()
    
    return {
        "message": "Alert rule created successfully",
        "rule": dict(row._mapping)
    }


@router.put("/{rule_id}")
async def update_alert_rule(
        rule_id: UUID,
        rule_data: dict,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Update existing alert rule (schema-routed)"""
    # Check if rule exists
    check = await db.execute(
        text("SELECT 1 FROM alert_rules WHERE rule_id = :rule_id"),
        {"rule_id": rule_id}
    )
    if not check.fetchone():
        raise HTTPException(status_code=404, detail="Alert rule not found")
    
    # Build update query
    updates = []
    params = {"rule_id": rule_id}
    
    for field in ['name', 'description', 'sensor_pattern', 'sensor_id', 'equipment_id',
                  'detection_type', 'condition', 'threshold', 'threshold_min', 'threshold_max',
                  'model_id', 'anomaly_threshold', 'severity', 'priority', 'enabled']:
        if field in rule_data:
            updates.append(f"{field} = :{field}")
            params[field] = rule_data[field]
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    query = f"""
        UPDATE alert_rules 
        SET {', '.join(updates)}, updated_at = NOW() 
        WHERE rule_id = :rule_id 
        RETURNING *
    """
    
    result = await db.execute(text(query), params)
    row = result.fetchone()
    await db.commit()
    
    return {
        "message": "Alert rule updated successfully",
        "rule": dict(row._mapping)
    }


@router.patch("/{rule_id}/detection-mode")
async def switch_detection_mode(
        rule_id: UUID,
        mode_data: dict,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Switch detection mode for an alert rule (schema-routed)"""
    # Check if rule exists
    check = await db.execute(
        text("SELECT detection_type FROM alert_rules WHERE rule_id = :rule_id"),
        {"rule_id": rule_id}
    )
    existing = check.fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    
    previous_mode = existing.detection_type
    new_mode = mode_data.get('detection_type')
    
    if not new_mode:
        raise HTTPException(status_code=400, detail="detection_type is required")
    
    # Update detection mode
    params = {
        "rule_id": rule_id,
        "detection_type": new_mode
    }
    
    updates = ["detection_type = :detection_type"]
    
    if mode_data.get('model_id'):
        updates.append("model_id = :model_id")
        params["model_id"] = mode_data['model_id']
    
    if mode_data.get('condition'):
        updates.append("condition = :condition")
        params["condition"] = mode_data['condition']
    
    query = f"""
        UPDATE alert_rules 
        SET {', '.join(updates)}, updated_at = NOW() 
        WHERE rule_id = :rule_id 
        RETURNING *
    """
    
    result = await db.execute(text(query), params)
    row = result.fetchone()
    await db.commit()
    
    return {
        "message": "Detection mode switched successfully",
        "previous_mode": previous_mode,
        "new_mode": new_mode,
        "rule": dict(row._mapping)
    }


@router.delete("/{rule_id}")
async def delete_alert_rule(
        rule_id: UUID,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
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
