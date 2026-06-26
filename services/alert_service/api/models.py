# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Pydantic models for alert system with ML support
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from uuid import UUID
from enum import Enum
from pydantic import BaseModel, Field, field_validator, ConfigDict
import uuid

# ============================================================================
# Base Model
# ============================================================================

class SafeBaseModel(BaseModel):
    """Base class for all Pydantic models with safe namespaces."""
    model_config = ConfigDict(protected_namespaces=(), populate_by_name=True)

# ============================================================================
# Enums
# ============================================================================

class DetectionType(str, Enum):
    """Alert detection method"""
    THRESHOLD = "threshold"
    ML = "ml"
    STATISTICAL = "statistical"


class Condition(str, Enum):
    """Threshold comparison conditions"""
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    BETWEEN = "between"
    OUTSIDE_RANGE = "outside_range"


class Severity(str, Enum):
    """Alert severity levels"""
    LOW = "low"
    MEDIUM = "medium"
    CRITICAL = "critical"
    HIGH = "high"


class ModelType(str, Enum):
    """ML model types"""
    ISOLATION_FOREST = "isolation_forest"
    LSTM = "lstm"
    AUTOENCODER = "autoencoder"
    STATISTICAL = "statistical"


class ModelStatus(str, Enum):
    """ML model status"""
    ACTIVE = "active"
    DEPRECATED = "deprecated"
    TRAINING = "training"
    FAILED = "failed"

# ============================================================================
# Alert Rule Models
# ============================================================================

class AlertRuleBase(SafeBaseModel):
    """Base alert rule fields (without company_id for creation)"""
    name: str
    description: Optional[str] = None

    @field_validator('sensor_id', 'equipment_id', mode='before')
    @classmethod
    def convert_optional_uuid_to_str(cls, v):
        """Convert UUID objects to strings, allow None"""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    # Target selection
    sensor_pattern: Optional[str] = None
    sensor_id: Optional[str] = None
    equipment_id: Optional[str] = None
    site_id: Optional[str] = None

    # Detection configuration
    detection_type: DetectionType

    # Threshold fields
    condition: Optional[Condition] = None
    threshold: Optional[float] = None
    threshold_min: Optional[float] = None
    threshold_max: Optional[float] = None

    # ML fields
    model_id: Optional[UUID] = None
    anomaly_threshold: Optional[float] = Field(default=0.85, ge=0.0, le=1.0)
    ml_model_config: Optional[Dict[str, Any]] = Field(default=None, alias="model_config")

    # Alert configuration
    severity: Severity
    priority: int = Field(default=0, ge=0)
    enabled: bool = True

    @field_validator('sensor_pattern', 'sensor_id')
    @classmethod
    def validate_target(cls, v, info):
        """At least one target (pattern or sensor_id) must be specified"""
        # This will be validated in the repository layer
        return v


class AlertRuleCreate(AlertRuleBase):
    """Model for creating new alert rules"""
    created_by: Optional[str] = None


class AlertRuleUpdate(SafeBaseModel):
    """Model for updating alert rules (all fields optional)"""
    name: Optional[str] = None
    description: Optional[str] = None

    sensor_pattern: Optional[str] = None
    sensor_id: Optional[str] = None
    equipment_id: Optional[str] = None
    site_id: Optional[str] = None

    detection_type: Optional[DetectionType] = None

    condition: Optional[Condition] = None
    threshold: Optional[float] = None
    threshold_min: Optional[float] = None
    threshold_max: Optional[float] = None

    model_id: Optional[UUID] = None
    anomaly_threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    ml_model_config: Optional[Dict[str, Any]] = Field(default=None, alias="model_config")

    severity: Optional[Severity] = None
    priority: Optional[int] = Field(default=None, ge=0)
    enabled: Optional[bool] = None


class AlertRule(AlertRuleBase):
    """Complete alert rule with metadata"""
    rule_id: UUID
    company_id: str  # Added for response model
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None

    @field_validator('company_id', mode='before')
    @classmethod
    def convert_uuid_to_str(cls, v):
        """Convert UUID objects to strings"""
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    class Config:
        from_attributes = True

# ============================================================================
# ML Model Models
# ============================================================================

class MLModelBase(SafeBaseModel):
    """Base ML model fields matching database schema"""
    equipment_id: Optional[UUID] = None
    model_name: str
    description: Optional[str] = None
    model_type: ModelType
    model_version: int = 1
    mlflow_run_id: Optional[str] = None
    mlflow_experiment_id: Optional[str] = None
    model_path: Optional[str] = None
    training_metrics: Optional[Dict[str, Any]] = None
    hyperparameters: Optional[Dict[str, Any]] = None
    feature_names: Optional[List[str]] = None
    sensor_ids: Optional[List[str]] = None
    accuracy: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    precision_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    recall: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    f1_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    auc_roc: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    training_samples: Optional[int] = None
    training_start_date: Optional[datetime] = None
    training_end_date: Optional[datetime] = None
    status: ModelStatus = ModelStatus.TRAINING
    deployed_at: Optional[datetime] = None
    deprecated_at: Optional[datetime] = None

class MLModelCreate(MLModelBase):
    """Model for creating new ML models"""
    created_by: Optional[str] = None


class MLModel(MLModelBase):
    """Complete ML model with metadata"""
    model_id: UUID
    created_at: datetime
    created_by: Optional[str] = None

    class Config:
        from_attributes = True

# ============================================================================
# Alert Models
# ============================================================================

class AlertBase(SafeBaseModel):
    """Base alert fields (without company_id for creation)"""
    rule_id: UUID
    sensor_id: Optional[str] = None
    equipment_id: Optional[str] = None
    site_id: Optional[str] = None

    detection_type: DetectionType

    # Threshold details
    threshold_value: Optional[float] = None
    actual_value: Optional[float] = None
    condition: Optional[str] = None

    # ML details
    model_id: Optional[UUID] = None
    anomaly_score: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    affected_sensors: Optional[List[str]] = None

    severity: Severity
    message: str

    @field_validator('sensor_id', 'equipment_id', mode='before')
    @classmethod
    def convert_optional_uuid_to_str(cls, v):
        """Convert UUID objects to strings, allow None"""
        if v is None:
            return None
        if isinstance(v, uuid.UUID):
            return str(v)
        return v


class AlertCreate(AlertBase):
    """Model for creating new alerts"""
    triggered_at: Optional[datetime] = None


class Alert(AlertBase):
    """Complete alert with metadata"""
    alert_id: UUID
    company_id: str  # Added for response model
    triggered_at: datetime
    acknowledged: bool = False
    acknowledged_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    created_at: datetime

    @field_validator('company_id', mode='before')
    @classmethod
    def convert_uuid_to_str(cls, v):
        """Convert UUID objects to strings"""
        if isinstance(v, uuid.UUID):
            return str(v)
        return v

    class Config:
        from_attributes = True


class AlertAcknowledge(SafeBaseModel):
    """Model for acknowledging alerts"""
    acknowledged_by: str

# ============================================================================
# Detection Mode Switch Model
# ============================================================================

class DetectionModeSwitch(SafeBaseModel):
    """Model for switching detection mode"""
    detection_type: DetectionType

    # Optional: new threshold configuration
    condition: Optional[Condition] = None
    threshold: Optional[float] = None
    threshold_min: Optional[float] = None
    threshold_max: Optional[float] = None

    # Optional: new ML configuration
    model_id: Optional[UUID] = None
    anomaly_threshold: Optional[float] = Field(default=0.85, ge=0.0, le=1.0)
    ml_model_config: Optional[Dict[str, Any]] = Field(default=None, alias="model_config")
