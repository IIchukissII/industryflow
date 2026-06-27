# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Optional
from uuid import UUID
from datetime import datetime
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
        start: Optional[datetime] = Query(None, description="Only measurements at/after this time (inclusive)"),
        end: Optional[datetime] = Query(None, description="Only measurements at/before this time (inclusive)"),
        order: str = Query("desc", pattern="^(asc|desc)$", description="Time order: 'asc' for analytics, 'desc' (default) for newest-first"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
        db: AsyncSession = Depends(get_db_with_tenant)
):
    """
    Get raw sensor measurements with optional filtering.
    Automatically routed to user's tenant schema.

    Defaults to the most recent measurements (newest first). Pass start/end to pull a time
    window and order=asc for time-series/analytics consumption. The per-request cap is 1000;
    for bulk historical pulls use /api/training-data/equipment/{id} (JSON) or its /stream (CSV).
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

    if start:
        query += " AND time >= :start"
        params["start"] = start

    if end:
        query += " AND time <= :end"
        params["end"] = end

    # order is validated by the route pattern, so this interpolation cannot carry injection.
    query += f" ORDER BY time {'ASC' if order == 'asc' else 'DESC'} LIMIT :limit"

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
        start: Optional[datetime] = Query(None, description="Only measurements at/after this time (inclusive)"),
        end: Optional[datetime] = Query(None, description="Only measurements at/before this time (inclusive)"),
        order: str = Query("desc", pattern="^(asc|desc)$", description="Time order: 'asc' for analytics, 'desc' (default) for newest-first"),
        limit: int = Query(100, ge=1, le=1000),
        db: AsyncSession = Depends(get_db_with_tenant)
):
    """Get measurements for a specific sensor, optionally within a [start, end] window."""
    query = """
        SELECT time, sensor_id, equipment_id, site_id,
               value, unit, quality_code
        FROM sensor_measurements
        WHERE sensor_id = :sensor_id
    """
    params = {"sensor_id": sensor_id, "limit": limit}

    if start:
        query += " AND time >= :start"
        params["start"] = start

    if end:
        query += " AND time <= :end"
        params["end"] = end

    # order is validated by the route pattern, so this interpolation cannot carry injection.
    query += f" ORDER BY time {'ASC' if order == 'asc' else 'DESC'} LIMIT :limit"

    result = await db.execute(
        text(query),
        params
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
