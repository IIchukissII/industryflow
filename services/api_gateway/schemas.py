# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

# src/api/schemas.py
import uuid
from enum import Enum
from typing import Any, Dict, Literal, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator
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
# Alert Rule Schemas (ADR-0031: the gateway is the alerting HTTP surface)
# ============================================================================
# The vocabulary is the alert_rules table's CHECK constraints and what the detector evaluates.
# A rule is validated as a whole: a create on its body, an update or mode switch on the stored row
# with the change applied, so a partial edit cannot leave a rule the detector would skip.

class DetectionType(str, Enum):
    THRESHOLD = "threshold"
    ML = "ml"
    STATISTICAL = "statistical"


class Condition(str, Enum):
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    BETWEEN = "between"
    OUTSIDE_RANGE = "outside_range"


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


# Writable rule columns. The tenant is never among them: it is the schema the request runs in
# (ADR-0003), so a company_id in the body is ignored.
RULE_FIELDS = (
    "name", "description", "sensor_pattern", "sensor_id", "equipment_id", "site_id",
    "detection_type", "condition", "threshold", "threshold_min", "threshold_max",
    "model_id", "anomaly_threshold", "model_config", "severity", "priority", "enabled",
)


class AlertRuleCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)

    name: str = Field(min_length=1)
    description: Optional[str] = None
    sensor_pattern: Optional[str] = None
    sensor_id: Optional[uuid.UUID] = None
    equipment_id: Optional[uuid.UUID] = None
    site_id: Optional[str] = None
    detection_type: DetectionType = DetectionType.THRESHOLD
    condition: Optional[Condition] = None
    threshold: Optional[float] = None
    threshold_min: Optional[float] = None
    threshold_max: Optional[float] = None
    model_id: Optional[uuid.UUID] = None
    anomaly_threshold: Optional[float] = Field(default=0.85, ge=0.0, le=1.0)
    ml_model_config: Optional[Dict[str, Any]] = Field(default=None, alias="model_config")
    severity: Severity = Severity.MEDIUM
    priority: int = Field(default=0, ge=0)
    enabled: bool = True

    @model_validator(mode="after")
    def _detection_requirements(self):
        # ML and drift (ADR-0021) rules are scored against a model; without one the detector
        # would skip the rule silently. A drift threshold is a share of drifted features.
        if self.detection_type in (DetectionType.ML, DetectionType.STATISTICAL) and self.model_id is None:
            raise ValueError(f"{self.detection_type.value} rules require a model_id")
        if self.detection_type == DetectionType.STATISTICAL and self.threshold is not None:
            if not 0.0 <= self.threshold <= 1.0:
                raise ValueError("statistical (drift) threshold must be a share in [0.0, 1.0]")
        return self

    def columns(self) -> Dict[str, Any]:
        """The rule as table columns (enum values, the model config under its column name)."""
        d = self.model_dump(mode="json", by_alias=True)
        return {k: d[k] for k in RULE_FIELDS}


class AlertRuleUpdate(BaseModel):
    """A partial edit. Only the fields sent are applied; the result is validated as a whole."""
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)

    name: Optional[str] = Field(default=None, min_length=1)
    description: Optional[str] = None
    sensor_pattern: Optional[str] = None
    sensor_id: Optional[uuid.UUID] = None
    equipment_id: Optional[uuid.UUID] = None
    site_id: Optional[str] = None
    detection_type: Optional[DetectionType] = None
    condition: Optional[Condition] = None
    threshold: Optional[float] = None
    threshold_min: Optional[float] = None
    threshold_max: Optional[float] = None
    model_id: Optional[uuid.UUID] = None
    anomaly_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ml_model_config: Optional[Dict[str, Any]] = Field(default=None, alias="model_config")
    severity: Optional[Severity] = None
    priority: Optional[int] = Field(default=None, ge=0)
    enabled: Optional[bool] = None

    def changes(self) -> Dict[str, Any]:
        d = self.model_dump(mode="json", by_alias=True, exclude_unset=True)
        return {k: d[k] for k in RULE_FIELDS if k in d}


class DetectionModeSwitch(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    detection_type: DetectionType
    model_id: Optional[uuid.UUID] = None
    condition: Optional[Condition] = None

    def changes(self) -> Dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


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