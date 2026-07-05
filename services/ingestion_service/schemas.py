# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pydantic schemas for sensor data ingestion.
"""
from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional
import uuid

class SensorDataInput(BaseModel):
    """
    Input schema for authenticated sensor data ingestion.
    company_id is NOT included here - it's read from the verified device certificate (ADR-0002).
    """
    timestamp: datetime = Field(..., description="Sensor reading timestamp")
    sensor_id: uuid.UUID = Field(..., description="Sensor UUID")
    equipment_id: uuid.UUID = Field(..., description="Equipment UUID")
    site_id: str = Field(..., min_length=1, max_length=100)
    value: float = Field(..., description="Sensor reading value")
    unit: Optional[str] = Field(None, max_length=20, description="Unit of measurement")
    quality_code: Optional[int] = Field(None, ge=0, le=2, description="0=good, 1=uncertain, 2=bad")
    
    model_config = ConfigDict(json_schema_extra={
        "example": {
            "timestamp": "2025-11-08T10:30:00Z",
            "sensor_id": "2e9bf157-d0f0-4618-9286-c792ee54c127",
            "equipment_id": "ef9791ba-4fba-4d78-ab76-eb7094933f99",
            "site_id": "factory_north",
            "value": 75.5,
            "unit": "celsius",
            "quality_code": 0
        }
    })

class SensorDataResponse(BaseModel):
    """Response after successful data ingestion"""
    status: str = "accepted"
    message: str
    company_id: str
    sensor_id: uuid.UUID
    timestamp: datetime
