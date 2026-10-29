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

import asyncpg

import auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/feature-configs", tags=["Feature Engineering"])


# ============================================================================
# Dependencies
# ============================================================================

# The caller's tenant, read from the verified token's company_id claim (ADR-0003 dec 1, 2); there is
# no tenant-schema scan to fall back to.
get_company_id_dependency = auth.get_company_id


async def _bound_refusal(request: Request, company_id: str, config_id: str) -> HTTPException:
    """The database refused a change to a configuration a model binds (ADR-0041 dec 2-3): say which."""
    models = await request.app.state.ml_repository.models_bound_to_feature_config(
        company_id=company_id, config_id=config_id,
    )
    names = ", ".join(f"{m['model_name']} ({m['id']})" for m in models) or "a model"
    return HTTPException(status_code=409, detail=(
        f"Feature config {config_id} is bound by {names}. A bound configuration keeps its sensors and "
        f"transforms and is not deleted; create a new configuration and register a model with it."
    ))


# ============================================================================
# Pydantic Models
# ============================================================================

class FeatureConfigCreate(BaseModel):
    """Request to create a feature engineering configuration"""
    equipment_type: str = Field(..., description="Equipment type (e.g., 'motor', 'pump', 'compressor')")
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

@router.post("", response_model=FeatureConfigResponse, status_code=201, dependencies=[Depends(auth.require_writer)])
async def create_feature_config(
    config: FeatureConfigCreate,
    request: Request,
    current_user: dict = Depends(auth.verify_jwt_token),
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Create a new feature engineering configuration

    This defines how raw sensor data should be transformed into ML features
    for a specific equipment type.
    """
    try:
        result = await request.app.state.ml_repository.create_feature_config(
            company_id=company_id,
            equipment_type=config.equipment_type,
            name=config.name,
            base_sensors=config.base_sensors,
            transformations=config.transformations,
            description=config.description,
            # Who created it: the authenticated user (the token's subject), not their company.
            created_by=str(current_user["user_id"])
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


@router.put("/{config_id}", response_model=FeatureConfigResponse, dependencies=[Depends(auth.require_writer)])
async def update_feature_config(
    config_id: str,
    update: FeatureConfigUpdate,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Update a feature engineering configuration; a bound one only in name, description and equipment
    type (ADR-0041 dec 2)
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
    except asyncpg.RestrictViolationError:
        raise await _bound_refusal(request, company_id, config_id)
    except Exception as e:
        logger.error(f"Error updating feature config {config_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{config_id}", status_code=204, dependencies=[Depends(auth.require_writer)])
async def delete_feature_config(
    config_id: str,
    request: Request,
    company_id: str = Depends(get_company_id_dependency)
):
    """
    Delete a feature engineering configuration no model binds (ADR-0041 dec 2, 4)
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
    except asyncpg.ForeignKeyViolationError:
        raise await _bound_refusal(request, company_id, config_id)
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
    options for a particular type of equipment (e.g., all pump configs).
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
