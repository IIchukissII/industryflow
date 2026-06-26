# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Optional
from datetime import datetime, timedelta
import logging
import csv
import io

from dependencies import get_db_with_tenant
from models.schemas import TrainingDataResponse, TrainingDataPoint
from models.user import User
from dependencies import get_current_user_with_company

router = APIRouter(prefix="/api/training-data", tags=["Training Data"])
logger = logging.getLogger(__name__)

@router.get("/equipment/{equipment_id}/stream")
async def stream_training_data_csv(
    equipment_id: str,
    current_user: User = Depends(get_current_user_with_company),
    lookback_days: int = Query(30, ge=1, le=365, description="Days of historical data"),
    min_quality: float = Query(0.8, ge=0.0, le=1.0, description="Minimum quality code (0-1)"),
    db: AsyncSession = Depends(get_db_with_tenant)
):
    """
    Stream historical sensor data as CSV for ML training.
    Automatically routed to user's tenant schema.
    
    Returns data from all sensors associated with the specified equipment.
    Memory-efficient streaming for large datasets.
    """
    # Verify equipment exists (automatic schema routing)
    verify_query = """
        SELECT equipment_id FROM equipment 
        WHERE equipment_id = :equipment_id
    """
    result = await db.execute(
        text(verify_query),
        {"equipment_id": equipment_id}
    )
    if not result.fetchone():
        raise HTTPException(
            status_code=404,
            detail=f"Equipment '{equipment_id}' not found"
        )
    
    # Calculate time range
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=lookback_days)
    
    logger.info(
        f"User {current_user.email} streaming training data for equipment {equipment_id}"
    )
    
    async def generate_csv():
        """Generator function that yields CSV data in chunks"""
        # Write CSV header
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(['time', 'sensor_id', 'sensor_type', 'value', 'quality_code', 'unit'])
        yield output.getvalue()
        output.seek(0)
        output.truncate(0)
        
        # Query data - NO junction table, direct sensor ownership
        data_query = """
            SELECT 
                sm.time,
                sm.sensor_id,
                s.sensor_type,
                sm.value,
                sm.quality_code,
                sm.unit
            FROM sensor_measurements sm
            JOIN sensors s ON sm.sensor_id = s.sensor_id
            WHERE s.equipment_id = :equipment_id
              AND sm.time >= :start_time
              AND sm.time <= :end_time
              AND sm.quality_code >= :min_quality
            ORDER BY sm.time ASC, sm.sensor_id
        """
        
        # Use server-side cursor for streaming
        result = await db.stream(
            text(data_query).execution_options(yield_per=1000),
            {
                "equipment_id": equipment_id,
                "start_time": start_time,
                "end_time": end_time,
                "min_quality": min_quality
            }
        )
        
        row_count = 0
        async for partition in result.partitions(1000):
            for row in partition:
                writer.writerow([
                    row[0].isoformat(),  # time
                    row[1],              # sensor_id
                    row[2],              # sensor_type
                    float(row[3]),       # value
                    int(row[4]),         # quality_code
                    row[5] or ''         # unit
                ])
                row_count += 1
            
            # Yield chunk
            yield output.getvalue()
            output.seek(0)
            output.truncate(0)
        
        logger.info(f"Streamed {row_count} rows for equipment {equipment_id}")
    
    # Return streaming response
    return StreamingResponse(
        generate_csv(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=training_data_{equipment_id}_{lookback_days}d.csv"
        }
    )

@router.get("/equipment/{equipment_id}", response_model=TrainingDataResponse)
async def get_training_data_for_equipment(
    equipment_id: str,
    current_user: User = Depends(get_current_user_with_company),
    lookback_days: int = Query(30, ge=1, le=365, description="Days of historical data"),
    min_quality: float = Query(0.8, ge=0.0, le=1.0, description="Minimum quality code (0-1)"),
    limit: int = Query(10000, ge=1, le=100000, description="Maximum rows to return"),
    db: AsyncSession = Depends(get_db_with_tenant)
):
    """
    Get historical sensor data for ML training (JSON, limited rows).
    For large datasets, use /stream endpoint instead.
    Automatically routed to user's tenant schema.
    """
    company_id_str = str(current_user.company_id)
    
    # Verify equipment exists (automatic schema routing)
    verify_query = """
        SELECT equipment_id FROM equipment 
        WHERE equipment_id = :equipment_id
    """
    result = await db.execute(
        text(verify_query),
        {"equipment_id": equipment_id}
    )
    if not result.fetchone():
        raise HTTPException(
            status_code=404,
            detail=f"Equipment '{equipment_id}' not found"
        )
    
    # Calculate time range
    end_time = datetime.utcnow()
    start_time = end_time - timedelta(days=lookback_days)
    
    # Query training data - NO junction table, direct sensor ownership
    data_query = """
        SELECT 
            sm.time,
            sm.sensor_id,
            s.sensor_type,
            sm.value,
            sm.quality_code,
            sm.unit
        FROM sensor_measurements sm
        JOIN sensors s ON sm.sensor_id = s.sensor_id
        WHERE s.equipment_id = :equipment_id
          AND sm.time >= :start_time
          AND sm.time <= :end_time
          AND sm.quality_code >= :min_quality
        ORDER BY sm.time ASC, sm.sensor_id
        LIMIT :limit
    """
    
    result = await db.execute(
        text(data_query),
        {
            "equipment_id": equipment_id,
            "start_time": start_time,
            "end_time": end_time,
            "min_quality": min_quality,
            "limit": limit
        }
    )
    rows = result.fetchall()
    
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"No training data found for equipment '{equipment_id}' in the last {lookback_days} days"
        )
    
    # Convert to response model
    data_points = [
        TrainingDataPoint(
            time=row[0],
            sensor_id=row[1],
            sensor_type=row[2],
            value=float(row[3]),
            quality_code=int(row[4]),
            unit=row[5]
        )
        for row in rows
    ]
    
    # Get unique sensors
    unique_sensors = list(set(point.sensor_id for point in data_points))
    
    logger.info(
        f"User {current_user.email} fetched {len(data_points)} training data points for equipment {equipment_id}"
    )
    
    return TrainingDataResponse(
        equipment_id=equipment_id,
        company_id=company_id_str,
        total_rows=len(data_points),
        date_range_start=data_points[0].time if data_points else start_time,
        date_range_end=data_points[-1].time if data_points else end_time,
        sensors=unique_sensors,
        data=data_points
    )
