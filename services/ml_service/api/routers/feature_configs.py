# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Feature Engineering Configurations Router
Endpoints for managing feature engineering configurations
"""
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
import logging

import auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feature-configs", tags=["Feature Engineering"])


# ============================================================================
# Dependencies
# ============================================================================

async def get_company_id_dependency(
    request: Request,
    current_user: dict = Depends(auth.verify_jwt_token)
) -> str:
    """Get company_id, preferring the JWT claim over a tenant-schema scan."""
    # Prefer the company_id claim from the JWT (ADR-0003 dec 2); avoids the per-request
    # tenant-schema scan below (review finding X2). Fall back to the scan for legacy tokens.
    claim = current_user.get("payload", {}).get("company_id")
    if claim:
        return str(claim)

    db_pool = request.app.state.db_pool
    user_id = current_user["user_id"]

    async with db_pool.acquire() as conn:
        # Get all tenant schemas
        schemas = await conn.fetch("""
            SELECT schema_name
            FROM information_schema.schemata
            WHERE schema_name LIKE 'tenant_%'
            ORDER BY schema_name
        """)

        # Search each tenant schema for the user
        for schema_row in schemas:
            schema_name = schema_row['schema_name']

            await conn.execute(f"SET search_path TO {schema_name}, public")

            company_id = await conn.fetchval(
                'SELECT company_id FROM "user" WHERE id = $1',
                user_id
            )

            if company_id:
                return str(company_id)

    raise HTTPException(status_code=404, detail="Company not found for user")


# ============================================================================
# Pydantic Models
# ============================================================================

class TransformationConfig(BaseModel):
    """Configuration for a single feature transformation"""
    name: str = Field(..., description="Feature name after transformation")
    type: str = Field(..., description="Transformation type: identity, polynomial, interaction, rolling_stat, etc.")
    sensor: Optional[str] = Field(None, description="Source sensor name (for single-sensor transformations)")
    sensors: Optional[List[str]] = Field(None, description="Source sensor names (for multi-sensor transformations)")
    params: Optional[Dict[str, Any]] = Field(None, description="Transformation parameters")


class FeatureConfigCreate(BaseModel):
    """Request to create a feature engineering configuration"""
    equipment_type: str = Field(..., description="Equipment type (e.g., 'tep_reactor', 'motor', 'pump')")
    name: str = Field(..., description="Configuration name")
    description: Optional[str] = Field(None, description="Configuration description")
    base_sensors: List[str] = Field(..., description="List of required base sensor names")
    transformations: List[Dict[str, Any]] = Field(..., description="List of transformation configurations")


class FeatureConfigUpdate(BaseModel):
    """Request to update a feature engineering configuration"""
    equipment_type: Optional[str] = Field(None, description="Equipment type")
    name: Optional[str] = Field(None, description="Configuration name")
    description: Optional[str] = Field(None, description="Configuration description")
    base_sensors: Optional[List[str]] = Field(None, description="List of required base sensor names")
    transformations: Optional[List[Dict[str, Any]]] = Field(None, description="List of transformation configurations")


class FeatureConfigResponse(BaseModel):
    """Feature engineering configuration response"""
    id: str
    company_id: str
    equipment_type: str
    name: str
    description: Optional[str]
    base_sensors: List[str]
    transformations: List[Dict[str, Any]]
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str]


class FeatureConfigListResponse(BaseModel):
    """List of feature engineering configurations"""
    total: int
    configs: List[FeatureConfigResponse]


# ============================================================================
# Endpoints
# ============================================================================

@router.post("", response_model=FeatureConfigResponse, status_code=201)
async def create_feature_config(
    config: FeatureConfigCreate,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Create a new feature engineering configuration

    This defines how raw sensor data should be transformed into ML features
    for a specific equipment type.
    """
    try:
        # company_id from dependency  # User ID from JWT

        result = await request.app.state.ml_repository.create_feature_config(
            company_id=company_id,
            equipment_type=config.equipment_type,
            name=config.name,
            base_sensors=config.base_sensors,
            transformations=config.transformations,
            description=config.description,
            created_by=company_id
        )

        logger.info(f"Created feature config: {result['id']} for {config.equipment_type}")
        return result

    except Exception as e:
        logger.error(f"Error creating feature config: {e}")
        if "unique_equipment_config" in str(e):
            raise HTTPException(
                status_code=400,
                detail=f"Feature config '{config.name}' already exists for equipment type '{config.equipment_type}'"
            )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("", response_model=FeatureConfigListResponse)
async def get_all_feature_configs(
    request: Request,
    equipment_type: Optional[str] = None,
    limit: int = 100,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Get all feature engineering configurations

    Optionally filter by equipment type.
    """
    try:
        # company_id from dependency

        if equipment_type:
            configs = await request.app.state.ml_repository.get_feature_configs_by_equipment_type(
                company_id=company_id,
                equipment_type=equipment_type
            )
        else:
            configs = await request.app.state.ml_repository.get_all_feature_configs(
                company_id=company_id,
                limit=limit
            )

        return {
            "total": len(configs),
            "configs": configs
        }

    except Exception as e:
        logger.error(f"Error fetching feature configs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{config_id}", response_model=FeatureConfigResponse)
async def get_feature_config(
    config_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Get a specific feature engineering configuration by ID
    """
    try:
        # company_id from dependency

        config = await request.app.state.ml_repository.get_feature_config_by_id(
            company_id=company_id,
            config_id=config_id
        )

        if not config:
            raise HTTPException(
                status_code=404,
                detail=f"Feature config {config_id} not found"
            )

        return config

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching feature config {config_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{config_id}", response_model=FeatureConfigResponse)
async def update_feature_config(
    config_id: str,
    update: FeatureConfigUpdate,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Update a feature engineering configuration
    """
    try:
        # company_id from dependency

        result = await request.app.state.ml_repository.update_feature_config(
            company_id=company_id,
            config_id=config_id,
            equipment_type=update.equipment_type,
            name=update.name,
            description=update.description,
            base_sensors=update.base_sensors,
            transformations=update.transformations
        )

        if not result:
            raise HTTPException(
                status_code=404,
                detail=f"Feature config {config_id} not found"
            )

        logger.info(f"Updated feature config: {config_id}")
        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating feature config {config_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{config_id}", status_code=204)
async def delete_feature_config(
    config_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Delete a feature engineering configuration

    Note: This will set feature_config_id to NULL for any models using this config.
    """
    try:
        # company_id from dependency

        success = await request.app.state.ml_repository.delete_feature_config(
            company_id=company_id,
            config_id=config_id
        )

        if not success:
            raise HTTPException(
                status_code=404,
                detail=f"Feature config {config_id} not found"
            )

        logger.info(f"Deleted feature config: {config_id}")
        return None

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting feature config {config_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/equipment/{equipment_type}", response_model=FeatureConfigListResponse)
async def get_configs_by_equipment_type(
    equipment_type: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Get all feature configs for a specific equipment type

    This is useful when you want to see all available feature engineering
    options for a particular type of equipment (e.g., all TEP reactor configs).
    """
    try:
        # company_id from dependency

        configs = await request.app.state.ml_repository.get_feature_configs_by_equipment_type(
            company_id=company_id,
            equipment_type=equipment_type
        )

        return {
            "total": len(configs),
            "configs": configs
        }

    except Exception as e:
        logger.error(f"Error fetching feature configs for {equipment_type}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
