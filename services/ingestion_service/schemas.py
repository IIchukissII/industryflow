# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pydantic schemas for sensor data ingestion.
"""
from pydantic import BaseModel, Field, ConfigDict, field_validator
from datetime import datetime, timedelta, timezone
from typing import Optional
import uuid

# How far ahead of this service's clock a reading's timestamp may be. A reading stamped further in
# the future is a device clock fault, and once on the stream it would drag the aggregation watermark
# ahead and close every live window early. The Spark jobs apply the same bound
# (services/spark_jobs/reading_guard.py; a test holds the two equal).
MAX_FUTURE_SKEW_SECONDS = 300

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

    @field_validator("timestamp")
    @classmethod
    def _not_in_the_future(cls, value: datetime) -> datetime:
        """Reject a timestamp beyond the clock-skew bound. A naive timestamp is read as UTC."""
        aware = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        if aware > datetime.now(timezone.utc) + timedelta(seconds=MAX_FUTURE_SKEW_SECONDS):
            raise ValueError(
                f"timestamp is more than {MAX_FUTURE_SKEW_SECONDS}s ahead of the server clock"
            )
        return value
    
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
