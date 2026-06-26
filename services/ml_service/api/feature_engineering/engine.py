# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Feature Engineering Engine
Applies transformations based on configuration from database
"""
import numpy as np
from typing import Dict, List, Any, Optional
import logging

# Feature transforms are resolved through the extension registry (ADR-0010): the platform's
# generic transforms are built-ins; domains register new types in their own modules.
from extensions import get_transform, TransformContext

logger = logging.getLogger(__name__)


class FeatureEngineeringEngine:
    """
    Applies feature transformations based on database configuration
    Configuration-driven, flexible feature engineering
    """

    def __init__(
        self,
        feature_config: Dict[str, Any],
        feature_store: Optional[Any] = None,
        equipment_id: Optional[str] = None
    ):
        """
        Initialize feature engineering engine with configuration

        Args:
            feature_config: Configuration dict from database with keys:
                - base_sensors: List[str]
                - transformations: List[Dict] with type, sensor(s), params
            feature_store: Optional Feature Store instance for historical statistics
            equipment_id: Optional equipment ID for Feature Store lookups
        """
        self.base_sensors = feature_config.get('base_sensors', [])
        self.transformations = feature_config.get('transformations', [])
        self.feature_names = [t['name'] for t in self.transformations]
        self.feature_store = feature_store
        self.equipment_id = equipment_id

        logger.info(f"Initialized FeatureEngineeringEngine: {len(self.base_sensors)} base sensors, {len(self.transformations)} transformations, Feature Store: {feature_store is not None}")

    async def transform(self, sensor_data: Dict[str, float]) -> np.ndarray:
        """
        Apply all transformations to raw sensor data

        Args:
            sensor_data: Dictionary mapping sensor names to values
                Example: {"temp_inlet": 72.5, "pressure_1": 4.2, ...}

        Returns:
            numpy array of engineered features in the order defined by transformations
        """
        features = []

        for transformation in self.transformations:
            try:
                feature_value = await self._apply_transformation(transformation, sensor_data)
                features.append(feature_value)
            except Exception as e:
                logger.warning(f"Failed to compute feature '{transformation['name']}': {e}")
                # Use 0.0 as fallback for missing features
                features.append(0.0)

        return np.array(features).reshape(1, -1)

    async def _apply_transformation(self, transformation: Dict[str, Any], sensor_data: Dict[str, float]) -> float:
        """
        Apply a single transformation by dispatching its ``type`` through the extension
        registry (ADR-0010). Built-in generic transforms and any loaded domain transforms
        are resolved the same way; the engine holds no transform-type knowledge itself.
        """
        t_type = transformation['type']
        fn = get_transform(t_type)
        if fn is None:
            logger.warning(f"Unknown transformation type: {t_type}")
            return 0.0
        ctx = TransformContext(feature_store=self.feature_store, equipment_id=self.equipment_id)
        return await fn(transformation, sensor_data, ctx)

    def get_feature_names(self) -> List[str]:
        """Get ordered list of feature names"""
        return self.feature_names

    def validate_sensor_data(self, sensor_data: Dict[str, float]) -> bool:
        """
        Check if sensor data contains all required base sensors

        Args:
            sensor_data: Dictionary of sensor readings

        Returns:
            True if all base sensors present, False otherwise
        """
        missing = []
        for sensor in self.base_sensors:
            if sensor not in sensor_data:
                missing.append(sensor)

        if missing:
            logger.warning(f"Missing {len(missing)} base sensors: {missing[:5]}...")
            return False

        return True
