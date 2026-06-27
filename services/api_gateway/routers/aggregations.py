# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from fastapi import APIRouter, Depends, Query, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import List, Optional
from datetime import datetime

from dependencies import get_db_with_tenant
from dependencies import get_current_user_with_company
from models.user import User
from models import SensorAggregation

router = APIRouter(prefix="/api/aggregations", tags=["Aggregations"])

# Valid aggregation windows
VALID_WINDOWS = ["1min", "5min", "1hour"]


@router.get("/{window}", response_model=List[SensorAggregation])
async def get_aggregations(
        window: str = Path(..., description="Aggregation window: 1min, 5min, or 1hour"),
        current_user: User = Depends(get_current_user_with_company),
        sensor_id: Optional[str] = Query(None, description="Filter by sensor ID"),
        equipment_id: Optional[str] = Query(None, description="Filter by equipment ID"),
        start: Optional[datetime] = Query(None, description="Only windows at/after this time (inclusive)"),
        end: Optional[datetime] = Query(None, description="Only windows at/before this time (inclusive)"),
        order: str = Query("desc", pattern="^(asc|desc)$", description="Time order: 'asc' for analytics, 'desc' (default) for newest-first"),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of results"),
        db: AsyncSession = Depends(get_db_with_tenant)
):
    """
    Get aggregated sensor statistics for specified time window.
    Automatically routed to user's tenant schema.

    Available windows:
    - 1min: 1-minute aggregations
    - 5min: 5-minute aggregations
    - 1hour: 1-hour aggregations

    Returns min, max, avg, and count for each time window. Pass start/end to pull a time
    range and order=asc for time-series consumption; the per-request cap is 1000.
    """
    # Validate window parameter
    if window not in VALID_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid window '{window}'. Must be one of: {', '.join(VALID_WINDOWS)}"
        )

    # Map window to table name
    table_name = f"sensor_aggregations_{window}"

    # Build query - JOIN with sensors and equipment to get unit and site_id
    query = f"""
        SELECT 
            a.time, 
            a.sensor_id, 
            a.equipment_id, 
            COALESCE(e.site_id, '') as site_id,
            a.avg_value, 
            a.min_value, 
            a.max_value, 
            a.count_values, 
            s.unit
        FROM {table_name} a
        LEFT JOIN sensors s ON a.sensor_id = s.sensor_id
        LEFT JOIN equipment e ON a.equipment_id = e.equipment_id
        WHERE 1=1
    """
    params = {"limit": limit}

    if sensor_id:
        query += " AND a.sensor_id = :sensor_id"
        params["sensor_id"] = sensor_id

    if equipment_id:
        query += " AND a.equipment_id = :equipment_id"
        params["equipment_id"] = equipment_id

    if start:
        query += " AND a.time >= :start"
        params["start"] = start

    if end:
        query += " AND a.time <= :end"
        params["end"] = end

    # order is validated by the route pattern, so this interpolation cannot carry injection.
    query += f" ORDER BY a.time {'ASC' if order == 'asc' else 'DESC'} LIMIT :limit"

    # Execute query
    result = await db.execute(text(query), params)
    rows = result.fetchall()

    # Convert to Pydantic models
    aggregations = [
        SensorAggregation(
            time=row[0],
            sensor_id=row[1],
            equipment_id=row[2],
            site_id=row[3],
            company_id=current_user.company_id,
            avg_value=row[4],
            min_value=row[5],
            max_value=row[6],
            count_values=row[7],
            unit=row[8]
        )
        for row in rows
    ]

    return aggregations


@router.get("/{window}/latest", response_model=List[SensorAggregation])
async def get_latest_aggregations(
        window: str = Path(..., description="Aggregation window: 1min, 5min, or 1hour"),
        current_user: User = Depends(get_current_user_with_company),
        limit: int = Query(100, ge=1, le=1000, description="Maximum number of results per sensor"),
        db: AsyncSession = Depends(get_db_with_tenant)
):
    """
    Get the latest aggregation for each sensor in the specified time window.
    Automatically routed to user's tenant schema.
    """
    # Validate window parameter
    if window not in VALID_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid window '{window}'. Must be one of: {', '.join(VALID_WINDOWS)}"
        )

    table_name = f"sensor_aggregations_{window}"

    query = f"""
        SELECT DISTINCT ON (a.sensor_id)
            a.time, 
            a.sensor_id, 
            a.equipment_id, 
            COALESCE(e.site_id, '') as site_id,
            a.avg_value, 
            a.min_value, 
            a.max_value, 
            a.count_values, 
            s.unit
        FROM {table_name} a
        LEFT JOIN sensors s ON a.sensor_id = s.sensor_id
        LEFT JOIN equipment e ON a.equipment_id = e.equipment_id
        ORDER BY a.sensor_id, a.time DESC
    """

    result = await db.execute(text(query))
    rows = result.fetchall()

    aggregations = [
        SensorAggregation(
            time=row[0],
            sensor_id=row[1],
            equipment_id=row[2],
            site_id=row[3],
            company_id=current_user.company_id,
            avg_value=row[4],
            min_value=row[5],
            max_value=row[6],
            count_values=row[7],
            unit=row[8]
        )
        for row in rows
    ]

    return aggregations


@router.get("/combined/{sensor_id}", response_model=dict)
async def get_combined_aggregations(
        current_user: User = Depends(get_current_user_with_company),
        sensor_id: str = Path(..., description="Sensor ID to fetch all timeframes for"),
        limit: int = Query(100, ge=1, le=500, description="Maximum results per timeframe"),
        db: AsyncSession = Depends(get_db_with_tenant)
):
    """
    Get aggregations for a specific sensor across all time windows.
    Automatically routed to user's tenant schema.
    
    Returns a dictionary with keys: '1min', '5min', '1hour'
    Each containing aggregated data for that timeframe.
    """
    result = {
        "sensor_id": sensor_id,
        "timeframes": {}
    }

    # Fetch data from all three aggregation tables
    for window in VALID_WINDOWS:
        table_name = f"sensor_aggregations_{window}"

        query = f"""
            SELECT 
                a.time, 
                a.sensor_id, 
                a.equipment_id, 
                a.avg_value, 
                a.min_value, 
                a.max_value, 
                a.count_values, 
                s.unit
            FROM {table_name} a
            LEFT JOIN sensors s ON a.sensor_id = s.sensor_id
            WHERE a.sensor_id = :sensor_id
            ORDER BY a.time DESC
            LIMIT :limit
        """

        db_result = await db.execute(
            text(query),
            {"sensor_id": sensor_id, "limit": limit}
        )
        rows = db_result.fetchall()

        aggregations = [
            {
                "time": row[0].isoformat(),
                "avg_value": float(row[3]),
                "min_value": float(row[4]),
                "max_value": float(row[5]),
                "count_values": int(row[6]),
                "unit": row[7]
            }
            for row in rows
        ]

        result["timeframes"][window] = {
            "data": aggregations,
            "count": len(aggregations)
        }

    return result
