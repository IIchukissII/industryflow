# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# src/api/schemas.py
import uuid
from typing import Literal, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    """
    Schema for reading user data (responses)
    Includes multi-tenant fields
    """
    company_id: uuid.UUID
    role: str


Role = Literal["admin", "engineer", "observer"]


class UserCreate(schemas.BaseUserCreate):
    """
    Internal create model handed to the user manager. Never bound to a request body: the
    tenant and role are set by the admin route, never by the caller (ADR-0004 dec 5).
    """
    company_id: uuid.UUID
    role: Role = "observer"


class UserSelfUpdate(schemas.BaseUserUpdate):
    """
    What a user may change about themselves: email and password. No tenant, no role, no flags
    (ADR-0003 dec 1: the tenant comes from the verified principal, so the principal cannot set it).
    Unknown fields are refused rather than silently dropped.
    """
    model_config = ConfigDict(extra="forbid")
    is_active: None = None
    is_superuser: None = None
    is_verified: None = None


class AdminUserCreate(BaseModel):
    """
    Admin-created user. company_id is honoured only for a superuser; a tenant admin always
    creates into their own company (ADR-0004 dec 5).
    """
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(..., min_length=8)
    role: Role = "observer"
    company_id: Optional[uuid.UUID] = None
    is_active: bool = True
    is_superuser: bool = False


class AdminUserUpdate(schemas.BaseUserUpdate):
    """
    Admin update of a user in the admin's own company. company_id and is_superuser are
    superuser-only (ADR-0004 dec 5).
    """
    model_config = ConfigDict(extra="forbid")
    password: Optional[str] = Field(None, min_length=8)
    role: Optional[Role] = None
    company_id: Optional[uuid.UUID] = None
    is_verified: None = None


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

    model_config = ConfigDict(json_schema_extra={
        "example": {
            "timestamp": "2025-10-11T10:30:00Z",
            "sensor_id": "2e9bf157-d0f0-4618-9286-c792ee54c127",
            "equipment_id": "ef9791ba-4fba-4d78-ab76-eb7094933f99",
            "site_id": "factory_north",
            "value": 75.5,
            "unit": "°C",
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