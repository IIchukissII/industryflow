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
                Example: {"xmeas_1": 2.5, "xmeas_2": 120.3, ...}

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
        Apply a single transformation

        Args:
            transformation: Transformation config with type, sensor(s), params
            sensor_data: Raw sensor readings

        Returns:
            Transformed feature value
        """
        t_type = transformation['type']
        params = transformation.get('params', {})

        if t_type == 'identity':
            # Pass-through transformation
            sensor = transformation['sensor']
            return sensor_data.get(sensor, 0.0)

        elif t_type == 'polynomial':
            # Power transformation: x^n
            sensor = transformation['sensor']
            power = params.get('power', 2)
            value = sensor_data.get(sensor, 0.0)
            return value ** power

        elif t_type == 'interaction':
            # Multi-sensor operations: ratio, difference, product, sum
            sensors = transformation['sensors']
            operation = params.get('operation', 'product')

            if len(sensors) < 2:
                logger.warning(f"Interaction requires at least 2 sensors, got {len(sensors)}")
                return 0.0

            sensor1_value = sensor_data.get(sensors[0], 0.0)
            sensor2_value = sensor_data.get(sensors[1], 0.0)

            if operation == 'ratio':
                # Avoid division by zero
                return sensor1_value / (sensor2_value + 1e-8)
            elif operation == 'difference':
                return sensor1_value - sensor2_value
            elif operation == 'product':
                return sensor1_value * sensor2_value
            elif operation == 'sum':
                return sensor1_value + sensor2_value
            else:
                logger.warning(f"Unknown interaction operation: {operation}")
                return 0.0

        elif t_type == 'deviation':
            # Deviation from baseline
            sensor = transformation['sensor']
            baseline = params.get('baseline', 0.0)
            value = sensor_data.get(sensor, 0.0)
            return value - baseline

        elif t_type == 'statistical':
            # Statistical transformations
            stat_type = params.get('stat_type', 'deviation_from_run_mean')
            sensor = transformation.get('sensor')

            if not sensor:
                logger.warning(f"Statistical feature '{transformation['name']}' missing sensor field")
                return 0.0

            value = sensor_data.get(sensor, 0.0)

            if stat_type == 'deviation_from_run_mean':
                # Use Feature Store to compute rolling mean for deviation
                if self.feature_store and self.equipment_id:
                    try:
                        # Get rolling mean from last 50 readings
                        rolling_mean = await self.feature_store.compute_rolling_mean(
                            equipment_id=self.equipment_id,
                            sensor_name=sensor,
                            window=50
                        )

                        if rolling_mean is not None:
                            # Compute deviation from rolling mean
                            deviation = value - rolling_mean
                            logger.debug(f"Feature '{transformation['name']}': value={value:.2f}, mean={rolling_mean:.2f}, deviation={deviation:.2f}")
                            return deviation
                        else:
                            # Not enough historical data, use current value
                            logger.debug(f"Feature '{transformation['name']}': insufficient data, using current value")
                            return value
                    except Exception as e:
                        logger.warning(f"Failed to compute rolling mean for {sensor}: {e}")
                        return value
                else:
                    # No Feature Store available, use current value
                    return value
            else:
                logger.warning(f"Unknown statistical type: {stat_type}")
                return 0.0

        elif t_type == 'rolling_stat':
            # Rolling statistics (requires historical data from Feature Store)
            # For now, not implemented - would need Feature Store integration
            logger.warning(f"Rolling statistics not yet implemented for feature '{transformation['name']}'")
            return 0.0

        else:
            logger.warning(f"Unknown transformation type: {t_type}")
            return 0.0

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
