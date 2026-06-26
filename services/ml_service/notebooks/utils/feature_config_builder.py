# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Feature Configuration Builder for Jupyter Notebooks

This utility helps data scientists easily create feature engineering configurations
while training models in Jupyter notebooks.

Example:
    from utils.feature_config_builder import FeatureConfigBuilder

    config = FeatureConfigBuilder("TEP Anomaly Detection", "tep_reactor")
    config.add_sensors(["xmeas_1", "xmeas_2", ..., "xmv_11"])
    config.add_identity("xmeas_1")
    config.add_polynomial("xmeas_7", power=2, name="xmeas_7_squared")
    config.add_ratio("xmeas_7", "xmeas_8")
    config.add_deviation("xmeas_1", baseline=2700)

    # Register with ML Service
    config_id = config.register(ml_service_url, jwt_token)
"""

import json
import requests
from typing import List, Optional, Dict, Any
from datetime import datetime


class FeatureConfigBuilder:
    """Builder class for creating feature engineering configurations"""

    def __init__(self, name: str, equipment_type: str, description: str = None, version: str = "1.0.0"):
        """
        Initialize feature config builder

        Args:
            name: Human-readable name for the configuration
            equipment_type: Type of equipment (e.g., "tep_reactor", "pump", "compressor")
            description: Optional description
            version: Semantic version (default: "1.0.0")
        """
        self.config = {
            "name": name,
            "description": description or f"Feature engineering for {equipment_type}",
            "equipment_type": equipment_type,
            "base_sensors": [],
            "transformations": [],
            "version": version,
            "status": "active"
        }
        self._feature_names = set()  # Track to avoid duplicates

    def add_sensors(self, sensors: List[str]) -> 'FeatureConfigBuilder':
        """
        Add base sensors to the configuration

        Args:
            sensors: List of sensor names

        Returns:
            Self for method chaining
        """
        self.config["base_sensors"].extend(sensors)
        return self

    def add_identity(self, sensor: str, name: Optional[str] = None) -> 'FeatureConfigBuilder':
        """
        Add identity transformation (pass-through)

        Args:
            sensor: Sensor name
            name: Feature name (defaults to sensor name)

        Returns:
            Self for method chaining
        """
        feature_name = name or sensor

        if feature_name in self._feature_names:
            raise ValueError(f"Feature '{feature_name}' already exists")

        self.config["transformations"].append({
            "name": feature_name,
            "type": "identity",
            "sensor": sensor
        })
        self._feature_names.add(feature_name)
        return self

    def add_polynomial(self, sensor: str, power: int, name: Optional[str] = None) -> 'FeatureConfigBuilder':
        """
        Add polynomial transformation (x^n)

        Args:
            sensor: Sensor name
            power: Exponent (2 for squared, 3 for cubed, etc.)
            name: Feature name (defaults to "{sensor}_power{power}")

        Returns:
            Self for method chaining
        """
        if power == 2:
            default_name = f"{sensor}_squared"
        elif power == 3:
            default_name = f"{sensor}_cubed"
        else:
            default_name = f"{sensor}_power{power}"

        feature_name = name or default_name

        if feature_name in self._feature_names:
            raise ValueError(f"Feature '{feature_name}' already exists")

        self.config["transformations"].append({
            "name": feature_name,
            "type": "polynomial",
            "sensor": sensor,
            "params": {"power": power}
        })
        self._feature_names.add(feature_name)
        return self

    def add_ratio(self, sensor1: str, sensor2: str, name: Optional[str] = None) -> 'FeatureConfigBuilder':
        """
        Add ratio interaction (sensor1 / sensor2)

        Args:
            sensor1: Numerator sensor
            sensor2: Denominator sensor
            name: Feature name (defaults to "{sensor1}_{sensor2}_ratio")

        Returns:
            Self for method chaining
        """
        feature_name = name or f"{sensor1}_{sensor2}_ratio"

        if feature_name in self._feature_names:
            raise ValueError(f"Feature '{feature_name}' already exists")

        self.config["transformations"].append({
            "name": feature_name,
            "type": "interaction",
            "sensors": [sensor1, sensor2],
            "params": {"operation": "ratio"}
        })
        self._feature_names.add(feature_name)
        return self

    def add_difference(self, sensor1: str, sensor2: str, name: Optional[str] = None) -> 'FeatureConfigBuilder':
        """
        Add difference interaction (sensor1 - sensor2)

        Args:
            sensor1: First sensor
            sensor2: Second sensor
            name: Feature name (defaults to "{sensor1}_{sensor2}_diff")

        Returns:
            Self for method chaining
        """
        feature_name = name or f"{sensor1}_{sensor2}_diff"

        if feature_name in self._feature_names:
            raise ValueError(f"Feature '{feature_name}' already exists")

        self.config["transformations"].append({
            "name": feature_name,
            "type": "interaction",
            "sensors": [sensor1, sensor2],
            "params": {"operation": "difference"}
        })
        self._feature_names.add(feature_name)
        return self

    def add_product(self, sensor1: str, sensor2: str, name: Optional[str] = None) -> 'FeatureConfigBuilder':
        """
        Add product interaction (sensor1 * sensor2)

        Args:
            sensor1: First sensor
            sensor2: Second sensor
            name: Feature name (defaults to "{sensor1}_{sensor2}_product")

        Returns:
            Self for method chaining
        """
        feature_name = name or f"{sensor1}_{sensor2}_product"

        if feature_name in self._feature_names:
            raise ValueError(f"Feature '{feature_name}' already exists")

        self.config["transformations"].append({
            "name": feature_name,
            "type": "interaction",
            "sensors": [sensor1, sensor2],
            "params": {"operation": "product"}
        })
        self._feature_names.add(feature_name)
        return self

    def add_deviation(self, sensor: str, baseline: float = None, name: Optional[str] = None) -> 'FeatureConfigBuilder':
        """
        Add statistical deviation transformation (sensor - baseline)

        Args:
            sensor: Sensor name
            baseline: Baseline value for deviation (optional, can be set later)
            name: Feature name (defaults to "{sensor}_deviation")

        Returns:
            Self for method chaining
        """
        feature_name = name or f"{sensor}_deviation"

        if feature_name in self._feature_names:
            raise ValueError(f"Feature '{feature_name}' already exists")

        self.config["transformations"].append({
            "name": feature_name,
            "type": "statistical",
            "sensor": sensor,
            "params": {
                "stat_type": "deviation_from_run_mean",
                "baseline": baseline
            }
        })
        self._feature_names.add(feature_name)
        return self

    def add_custom(self, feature_name: str, transformation: Dict[str, Any]) -> 'FeatureConfigBuilder':
        """
        Add custom transformation

        Args:
            feature_name: Name of the feature
            transformation: Complete transformation definition

        Returns:
            Self for method chaining
        """
        if feature_name in self._feature_names:
            raise ValueError(f"Feature '{feature_name}' already exists")

        transformation["name"] = feature_name
        self.config["transformations"].append(transformation)
        self._feature_names.add(feature_name)
        return self

    def get_feature_names(self) -> List[str]:
        """
        Get list of all feature names in order

        Returns:
            List of feature names
        """
        return [t["name"] for t in self.config["transformations"]]

    def to_json(self, pretty: bool = True) -> str:
        """
        Export configuration as JSON string

        Args:
            pretty: If True, format with indentation

        Returns:
            JSON string
        """
        if pretty:
            return json.dumps(self.config, indent=2)
        return json.dumps(self.config)

    def to_dict(self) -> Dict[str, Any]:
        """
        Export configuration as dictionary

        Returns:
            Configuration dictionary
        """
        return self.config.copy()

    def save(self, filepath: str) -> None:
        """
        Save configuration to JSON file

        Args:
            filepath: Path to save JSON file
        """
        with open(filepath, 'w') as f:
            json.dump(self.config, f, indent=2)
        print(f"✓ Feature configuration saved to: {filepath}")

    def register(self, ml_service_url: str, jwt_token: str) -> str:
        """
        Register feature configuration with ML Service API

        Args:
            ml_service_url: ML Service base URL (e.g., "http://ml-service-api:8002")
            jwt_token: JWT authentication token

        Returns:
            Feature configuration ID (UUID)

        Raises:
            requests.HTTPError: If registration fails
        """
        headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json"
        }

        print(f"Registering feature configuration: {self.config['name']}")
        print(f"  Equipment type: {self.config['equipment_type']}")
        print(f"  Base sensors: {len(self.config['base_sensors'])}")
        print(f"  Transformations: {len(self.config['transformations'])}")

        response = requests.post(
            f"{ml_service_url}/api/feature-configs",
            headers=headers,
            json=self.config
        )

        if response.status_code == 201:
            result = response.json()
            config_id = result['id']
            print(f"✓ Feature configuration registered successfully!")
            print(f"  Config ID: {config_id}")
            print(f"  Created at: {result['created_at']}")
            return config_id
        else:
            print(f"✗ Failed to register feature configuration")
            print(f"  Status: {response.status_code}")
            print(f"  Error: {response.text}")
            response.raise_for_status()

    def summary(self) -> str:
        """
        Get human-readable summary of configuration

        Returns:
            Summary string
        """
        lines = []
        lines.append("=" * 80)
        lines.append(f"Feature Configuration: {self.config['name']}")
        lines.append("=" * 80)
        lines.append(f"Equipment Type: {self.config['equipment_type']}")
        lines.append(f"Version: {self.config['version']}")
        lines.append(f"Status: {self.config['status']}")
        lines.append("")
        lines.append(f"Base Sensors: {len(self.config['base_sensors'])}")
        lines.append(f"Transformations: {len(self.config['transformations'])}")
        lines.append("")

        # Count by type
        type_counts = {}
        for t in self.config["transformations"]:
            trans_type = t["type"]
            type_counts[trans_type] = type_counts.get(trans_type, 0) + 1

        lines.append("Transformation Breakdown:")
        for trans_type, count in sorted(type_counts.items()):
            lines.append(f"  {trans_type}: {count}")

        lines.append("")
        lines.append("Sample Features:")
        for i, t in enumerate(self.config["transformations"][:5]):
            lines.append(f"  {i+1}. {t['name']} ({t['type']})")

        if len(self.config["transformations"]) > 5:
            lines.append(f"  ... and {len(self.config['transformations']) - 5} more")

        lines.append("=" * 80)
        return "\n".join(lines)

    def __repr__(self) -> str:
        return f"FeatureConfigBuilder(name='{self.config['name']}', features={len(self.config['transformations'])})"


def load_config(filepath: str) -> FeatureConfigBuilder:
    """
    Load feature configuration from JSON file

    Args:
        filepath: Path to JSON file

    Returns:
        FeatureConfigBuilder instance
    """
    with open(filepath, 'r') as f:
        config_dict = json.load(f)

    builder = FeatureConfigBuilder(
        name=config_dict['name'],
        equipment_type=config_dict['equipment_type'],
        description=config_dict.get('description'),
        version=config_dict.get('version', '1.0.0')
    )

    builder.config = config_dict
    builder._feature_names = {t['name'] for t in config_dict['transformations']}

    return builder


# Convenience functions for common patterns

def create_tep_config(name: str = "TEP Binary Anomaly Detection") -> FeatureConfigBuilder:
    """
    Create feature config builder pre-configured for TEP process

    Args:
        name: Configuration name

    Returns:
        FeatureConfigBuilder with TEP sensors
    """
    config = FeatureConfigBuilder(name, "tep_reactor")

    # Add all 52 TEP sensors
    xmeas_sensors = [f"xmeas_{i}" for i in range(1, 42)]
    xmv_sensors = [f"xmv_{i}" for i in range(1, 12)]
    config.add_sensors(xmeas_sensors + xmv_sensors)

    return config


def batch_add_identities(config: FeatureConfigBuilder, sensors: List[str]) -> FeatureConfigBuilder:
    """
    Add identity transformations for multiple sensors

    Args:
        config: FeatureConfigBuilder instance
        sensors: List of sensor names

    Returns:
        Updated config (for chaining)
    """
    for sensor in sensors:
        config.add_identity(sensor)
    return config


def batch_add_polynomials(config: FeatureConfigBuilder, sensors: List[str], powers: List[int]) -> FeatureConfigBuilder:
    """
    Add polynomial transformations for multiple sensors and powers

    Args:
        config: FeatureConfigBuilder instance
        sensors: List of sensor names
        powers: List of powers to apply

    Returns:
        Updated config (for chaining)
    """
    for sensor in sensors:
        for power in powers:
            config.add_polynomial(sensor, power)
    return config


def batch_add_interactions(config: FeatureConfigBuilder, sensor_pairs: List[tuple],
                           operations: List[str] = None) -> FeatureConfigBuilder:
    """
    Add interaction transformations for multiple sensor pairs

    Args:
        config: FeatureConfigBuilder instance
        sensor_pairs: List of (sensor1, sensor2) tuples
        operations: List of operations (default: ['ratio', 'difference', 'product'])

    Returns:
        Updated config (for chaining)
    """
    if operations is None:
        operations = ['ratio', 'difference', 'product']

    for sensor1, sensor2 in sensor_pairs:
        if 'ratio' in operations:
            config.add_ratio(sensor1, sensor2)
        if 'difference' in operations:
            config.add_difference(sensor1, sensor2)
        if 'product' in operations:
            config.add_product(sensor1, sensor2)

    return config
