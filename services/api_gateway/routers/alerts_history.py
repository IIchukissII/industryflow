"""
REST API endpoints for alert history management with schema-per-tenant routing
"""
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Optional
from uuid import UUID
from datetime import datetime
from pydantic import BaseModel
from enum import Enum

from dependencies import get_db_with_tenant, get_current_user_with_company
from models.user import User

router = APIRouter(prefix="/api/alerts", tags=["Alerts History"])


# Local models
class Severity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class AlertResponse(BaseModel):
    alert_id: str
    rule_id: str
    sensor_id: Optional[str]
    equipment_id: Optional[str]
    company_id: str
    severity: str
    message: str
    actual_value: Optional[float]
    threshold_value: Optional[float]
    anomaly_score: Optional[float]
    triggered_at: datetime
    acknowledged: bool
    acknowledged_at: Optional[datetime]
    acknowledged_by: Optional[str]


@router.get("", response_model=List[AlertResponse])
async def list_alerts(
        sensor_id: Optional[str] = None,
        equipment_id: Optional[str] = None,
        severity: Optional[Severity] = None,
        acknowledged: Optional[bool] = None,
        limit: int = 100,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """
    List recent alerts for the authenticated user's company (schema-routed)
    
    Query Parameters:
    - sensor_id: Filter by specific sensor
    - equipment_id: Filter by equipment
    - severity: Filter by severity (info, low, medium, high, critical)
    - acknowledged: Filter by acknowledgment status
    - limit: Maximum number of alerts to return (default: 100, max: 1000)
    """
    # Enforce max limit
    if limit > 1000:
        limit = 1000
    
    # Build query - automatic schema routing
    query = """
        SELECT 
            alert_id::text,
            rule_id::text,
            sensor_id::text,
            equipment_id::text,
            severity,
            message,
            actual_value,
            threshold_value,
            anomaly_score,
            triggered_at,
            acknowledged,
            acknowledged_at,
            acknowledged_by
        FROM alerts
        WHERE 1=1
    """
    
    params = {"limit": limit}
    
    if sensor_id:
        query += " AND sensor_id = :sensor_id"
        params["sensor_id"] = sensor_id
    
    if equipment_id:
        query += " AND equipment_id = :equipment_id"
        params["equipment_id"] = equipment_id
    
    if severity:
        query += " AND severity = :severity"
        params["severity"] = severity.value
    
    if acknowledged is not None:
        query += " AND acknowledged = :acknowledged"
        params["acknowledged"] = acknowledged
    
    query += " ORDER BY triggered_at DESC LIMIT :limit"
    
    result = await db.execute(text(query), params)
    rows = result.fetchall()
    
    # Add company_id from current_user to each response
    alerts = []
    for row in rows:
        alert_dict = dict(row._mapping)
        alert_dict['company_id'] = str(current_user.company_id)
        alerts.append(alert_dict)
    
    return alerts


@router.get("/unacknowledged", response_model=List[AlertResponse])
async def list_unacknowledged_alerts(
        limit: int = 50,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Get unacknowledged alerts for the authenticated user's company (schema-routed)"""
    query = """
        SELECT 
            alert_id::text,
            rule_id::text,
            sensor_id::text,
            equipment_id::text,
            severity,
            message,
            actual_value,
            threshold_value,
            anomaly_score,
            triggered_at,
            acknowledged,
            acknowledged_at,
            acknowledged_by
        FROM alerts
        WHERE acknowledged = FALSE
        ORDER BY triggered_at DESC
        LIMIT :limit
    """
    
    result = await db.execute(text(query), {"limit": limit})
    rows = result.fetchall()
    
    # Add company_id from current_user to each response
    alerts = []
    for row in rows:
        alert_dict = dict(row._mapping)
        alert_dict['company_id'] = str(current_user.company_id)
        alerts.append(alert_dict)
    
    return alerts


@router.get("/critical", response_model=List[AlertResponse])
async def list_critical_alerts(
        limit: int = 50,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Get critical alerts for the authenticated user's company (schema-routed)"""
    query = """
        SELECT 
            alert_id::text,
            rule_id::text,
            sensor_id::text,
            equipment_id::text,
            severity,
            message,
            actual_value,
            threshold_value,
            anomaly_score,
            triggered_at,
            acknowledged,
            acknowledged_at,
            acknowledged_by
        FROM alerts
        WHERE severity = 'critical'
        ORDER BY triggered_at DESC
        LIMIT :limit
    """
    
    result = await db.execute(text(query), {"limit": limit})
    rows = result.fetchall()
    
    # Add company_id from current_user to each response
    alerts = []
    for row in rows:
        alert_dict = dict(row._mapping)
        alert_dict['company_id'] = str(current_user.company_id)
        alerts.append(alert_dict)
    
    return alerts


@router.patch("/{alert_id}/acknowledge")
async def acknowledge_alert(
        alert_id: UUID,
        db: AsyncSession = Depends(get_db_with_tenant),
        current_user: User = Depends(get_current_user_with_company)
):
    """Acknowledge an alert (schema-routed)"""
    result = await db.execute(text("""
        UPDATE alerts 
        SET acknowledged = TRUE,
            acknowledged_at = NOW(),
            acknowledged_by = :user_id
        WHERE alert_id = :alert_id
        RETURNING *
    """), {
        "alert_id": alert_id,
        "user_id": str(current_user.id)
    })
    
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found")
    
    await db.commit()
    
    return {
        "message": "Alert acknowledged successfully",
        "alert": dict(row._mapping)
    }
