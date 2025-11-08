from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Optional
from uuid import UUID
import logging

from dependencies import get_db_with_tenant
from models.schemas import SensorMeasurement
from models.user import User
from dependencies import get_current_user_with_company

router = APIRouter(prefix="/api/measurements", tags=["Measurements"])
logger = logging.getLogger(__name__)

@router.get("", response_model=List[SensorMeasurement])
async def get_measurements(
        current_user: User = Depends(get_current_user_with_company),
        sensor_id: Optional[UUID] = Query(None, description="Filter by sensor ID"),
        equipment_id: Optional[str] = Query(None, description="Filter by equipment ID"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
        db: AsyncSession = Depends(get_db_with_tenant)
):
    """
    Get raw sensor measurements with optional filtering.
    Automatically routed to user's tenant schema.
    Returns the most recent measurements ordered by timestamp (newest first).
    """
    query = """
        SELECT time, sensor_id, equipment_id, site_id, 
               value, unit, quality_code
        FROM sensor_measurements
        WHERE 1=1
    """
    params = {"limit": limit}

    if sensor_id:
        query += " AND sensor_id = :sensor_id"
        params["sensor_id"] = sensor_id

    if equipment_id:
        query += " AND equipment_id = :equipment_id"
        params["equipment_id"] = equipment_id

    query += " ORDER BY time DESC LIMIT :limit"

    result = await db.execute(text(query), params)
    rows = result.fetchall()

    logger.info(f"Query returned {len(rows)} rows")

    measurements = [
        SensorMeasurement(
            time=row[0],
            sensor_id=row[1],
            equipment_id=str(row[2]) if row[2] else None,
            site_id=row[3],
            company_id=current_user.company_id,
            value=row[4],
            unit=row[5],
            quality_code=row[6]
        )
        for row in rows
    ]

    return measurements


@router.get("/latest", response_model=List[SensorMeasurement])
async def get_latest_measurements(
        current_user: User = Depends(get_current_user_with_company),
        db: AsyncSession = Depends(get_db_with_tenant)
):
    """
    Get the latest measurement for each sensor.
    Automatically routed to user's tenant schema.
    Uses DISTINCT ON to get the most recent value per sensor.
    """
    query = """
        SELECT DISTINCT ON (sensor_id)
            time, sensor_id, equipment_id, site_id, 
            value, unit, quality_code
        FROM sensor_measurements
        ORDER BY sensor_id, time DESC
    """

    result = await db.execute(text(query))
    rows = result.fetchall()

    measurements = [
        SensorMeasurement(
            time=row[0],
            sensor_id=row[1],
            equipment_id=str(row[2]) if row[2] else None,
            site_id=row[3],
            company_id=current_user.company_id,
            value=row[4],
            unit=row[5],
            quality_code=row[6]
        )
        for row in rows
    ]

    return measurements

@router.get("/{sensor_id}", response_model=List[SensorMeasurement])
async def get_measurements_by_sensor(
        sensor_id: UUID,
        current_user: User = Depends(get_current_user_with_company),
        limit: int = Query(100, ge=1, le=1000),
        db: AsyncSession = Depends(get_db_with_tenant)
):
    """Get measurements for a specific sensor"""
    query = """
        SELECT time, sensor_id, equipment_id, site_id, 
               value, unit, quality_code
        FROM sensor_measurements
        WHERE sensor_id = :sensor_id
        ORDER BY time DESC
        LIMIT :limit
    """
    result = await db.execute(
        text(query), 
        {"sensor_id": sensor_id, "limit": limit}
    )
    rows = result.fetchall()
    
    measurements = [
        SensorMeasurement(
            time=row[0],
            sensor_id=row[1],
            equipment_id=str(row[2]) if row[2] else None,
            site_id=row[3],
            company_id=current_user.company_id,
            value=row[4],
            unit=row[5],
            quality_code=row[6]
        )
        for row in rows
    ]
    return measurements
