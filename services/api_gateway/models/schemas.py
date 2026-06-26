# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime
from typing import Optional, List


class SensorMeasurement(BaseModel):
    """Single sensor measurement"""
    time: datetime
    sensor_id: UUID
    equipment_id: UUID
    site_id: str
    company_id: UUID
    value: float
    unit: Optional[str] = None
    quality_code: Optional[int] = 1  # Changed to Optional to handle NULL values

    class Config:
        from_attributes = True


class SensorAggregation(BaseModel):
    """Aggregated sensor statistics"""
    time: datetime  # Changed from window_start/window_end
    sensor_id: UUID
    equipment_id: UUID
    site_id: str
    company_id: UUID
    avg_value: float
    min_value: float
    max_value: float
    count_values: int
    unit: Optional[str] = None

    class Config:
        from_attributes = True


class SensorDataRequest(BaseModel):
    """Request parameters for querying sensor data"""
    sensor_ids: Optional[List[str]] = None
    equipment_ids: Optional[List[str]] = None
    company_id: UUID = "acme_manufacturing"
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: int = Field(default=1000, ge=1, le=10000)


class HealthCheck(BaseModel):
    """API health check response"""
    status: str
    database: str
    timestamp: datetime
class TrainingDataPoint(BaseModel):
    """Single data point for ML training"""
    time: datetime
    sensor_id: UUID
    sensor_type: str
    value: float
    quality_code: int
    unit: Optional[str] = None
    
    class Config:
        from_attributes = True

class TrainingDataResponse(BaseModel):
    """Response containing training data"""
    equipment_id: UUID
    company_id: UUID
    total_rows: int
    date_range_start: datetime
    date_range_end: datetime
    sensors: List[str]
    data: List[TrainingDataPoint]
