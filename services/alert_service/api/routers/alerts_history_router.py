"""
Alerts History Router
Schema-per-tenant architecture with JWT-based company_id
"""
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from typing import List, Optional
from datetime import datetime
import asyncpg
import sys
from pathlib import Path

# Add parent directory to path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

from models import Alert, AlertAcknowledge
from repository import AlertRepository
from auth import get_company_id
from config import normalize_company_id_to_schema

router = APIRouter(prefix="/api/alerts", tags=["Alerts History"])


async def get_db_pool(request: Request):
    """Get database pool from app state"""
    db_pool = request.app.state.db_pool
    if not db_pool:
        raise HTTPException(status_code=503, detail="Database not available")
    return db_pool


@router.get("", response_model=List[Alert])
async def list_alerts(
    company_id: str = Depends(get_company_id),
    sensor_id: Optional[str] = None,
    equipment_id: Optional[str] = None,
    severity: Optional[str] = None,
    detection_type: Optional[str] = None,
    acknowledged: Optional[bool] = None,
    limit: int = Query(100, ge=1, le=1000),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """
    List alerts for authenticated user's company.
    Automatically filtered by company_id from JWT token.
    """
    repo = AlertRepository(db_pool)

    filters = {}
    if sensor_id:
        filters['sensor_id'] = sensor_id
    if equipment_id:
        filters['equipment_id'] = equipment_id
    if severity:
        filters['severity'] = severity
    if detection_type:
        filters['detection_type'] = detection_type
    if acknowledged is not None:
        filters['acknowledged'] = acknowledged
    filters['limit'] = limit

    alerts = await repo.get_alerts(company_id, filters)
    return alerts


@router.get("/unacknowledged", response_model=List[Alert])
async def get_unacknowledged_alerts(
    company_id: str = Depends(get_company_id),
    limit: int = Query(100, ge=1, le=1000),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Get all unacknowledged alerts for user's company"""
    repo = AlertRepository(db_pool)
    alerts = await repo.get_alerts(company_id, {
        'acknowledged': False,
        'limit': limit
    })
    return alerts


@router.get("/critical", response_model=List[Alert])
async def get_critical_alerts(
    company_id: str = Depends(get_company_id),
    limit: int = Query(100, ge=1, le=1000),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Get all critical severity alerts for user's company"""
    repo = AlertRepository(db_pool)
    alerts = await repo.get_alerts(company_id, {
        'severity': 'critical',
        'limit': limit
    })
    return alerts


@router.post("/{alert_id}/acknowledge", status_code=200)
async def acknowledge_alert(
    alert_id: str,
    ack_data: AlertAcknowledge,
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Acknowledge an alert"""
    repo = AlertRepository(db_pool)

    try:
        success = await repo.acknowledge_alert(
            alert_id, 
            ack_data.acknowledged_by,
            company_id
        )
        
        if not success:
            raise HTTPException(status_code=404, detail="Alert not found")
        
        return {"success": True, "alert_id": alert_id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to acknowledge alert: {str(e)}")


@router.get("/stats", response_model=dict)
async def get_alert_statistics(
    company_id: str = Depends(get_company_id),
    db_pool: asyncpg.Pool = Depends(get_db_pool)
):
    """Get alert statistics for user's company"""
    schema_name = normalize_company_id_to_schema(company_id)
    
    async with db_pool.acquire() as conn:
        await conn.execute(f"SET search_path TO {schema_name}, public")
        
        # Total alerts
        total = await conn.fetchval("SELECT COUNT(*) FROM alerts")

        # By severity
        severity_stats = await conn.fetch("""
            SELECT severity, COUNT(*) as count
            FROM alerts
            GROUP BY severity
        """)

        # By detection type
        detection_stats = await conn.fetch("""
            SELECT detection_type, COUNT(*) as count
            FROM alerts
            GROUP BY detection_type
        """)

        # Acknowledged vs unacknowledged
        ack_stats = await conn.fetch("""
            SELECT acknowledged, COUNT(*) as count
            FROM alerts
            GROUP BY acknowledged
        """)

        return {
            "total_alerts": total,
            "by_severity": {row['severity']: row['count'] for row in severity_stats},
            "by_detection_type": {row['detection_type']: row['count'] for row in detection_stats},
            "by_acknowledgment": {
                "acknowledged" if row['acknowledged'] else "unacknowledged": row['count']
                for row in ack_stats
            }
        }
