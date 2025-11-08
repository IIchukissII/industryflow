# src/api/schemas.py
import uuid
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, Field
from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    """
    Schema for reading user data (responses)
    Includes multi-tenant fields
    """
    company_id: uuid.UUID
    role: str


class UserCreate(schemas.BaseUserCreate):
    """
    Schema for creating new users
    Admin must provide company_id and role
    """
    company_id: uuid.UUID
    role: str = "observer"  # Default role


class UserUpdate(schemas.BaseUserUpdate):
    """
    Schema for updating user data
    Allows updating role and company_id (for admin transfers)
    """
    company_id: Optional[uuid.UUID] = None
    role: Optional[str] = None


# ============================================================================
# Sensor Data Ingestion Schemas
# ============================================================================

class SensorDataInput(BaseModel):
    """
    Input schema for authenticated sensor data ingestion.
    company_id is NOT included here - it's forced from JWT token.
    """
    timestamp: datetime = Field(..., description="Sensor reading timestamp")
    sensor_id: uuid.UUID = Field(..., description="Sensor UUID")
    equipment_id: uuid.UUID = Field(..., description="Equipment UUID")
    site_id: str = Field(..., min_length=1, max_length=100)
    value: float = Field(..., description="Sensor reading value")
    unit: Optional[str] = Field(None, max_length=20, description="Unit of measurement")
    quality_code: Optional[int] = Field(None, ge=0, le=2, description="0=good, 1=uncertain, 2=bad")

    class Config:
        json_schema_extra = {
            "example": {
                "timestamp": "2025-10-11T10:30:00Z",
                "sensor_id": "2e9bf157-d0f0-4618-9286-c792ee54c127",
                "equipment_id": "ef9791ba-4fba-4d78-ab76-eb7094933f99",
                "site_id": "factory_north",
                "value": 75.5,
                "unit": "°C",
                "quality_code": 0
            }
        }


class SensorDataResponse(BaseModel):
    """Response after successful data ingestion"""
    status: str = "accepted"
    message: str
    company_id: str
    sensor_id: uuid.UUID
    timestamp: datetime