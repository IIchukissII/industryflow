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
# generic transforms are built-ins; domains register new types in their own modules. The registry
# also carries each type's capability tags (ADR-0024), which is how the engine can neutralize the
# stateful class without knowing which types are in it.
from extensions import (
    FeatureRefused, MissingSensors, StatefulUnavailable, TransformContext,
    get_transform, is_stateful, missing_sensors, neutral_value,
)

logger = logging.getLogger(__name__)



class UnknownTransform(FeatureRefused):
    """A feature configuration names a transform type no registered transform implements."""

    def __init__(self, types):
        self.types = list(types)
        super().__init__(f"the feature configuration names transform types this deployment does not have: "
                         f"{', '.join(self.types)}")

class FeatureEngineeringEngine:
    """
    Applies feature transformations based on database configuration
    Configuration-driven, flexible feature engineering
    """

    def __init__(
        self,
        feature_config: Dict[str, Any],
        baseline_provider: Optional[Any] = None,
        equipment_id: Optional[str] = None,
        company_id: Optional[str] = None,
        kill_switch: Optional[Any] = None
    ):
        """
        Initialize feature engineering engine with configuration

        Args:
            feature_config: Configuration dict from database with keys:
                - base_sensors: List[str]
                - transformations: List[Dict] with type, sensor(s), params
            baseline_provider: Optional provider of windowed baselines from the Spark
                aggregates (ADR-0023), used by stateful transforms
            equipment_id: Optional equipment ID for baseline lookups
            company_id: Optional tenant company_id, scoping baseline lookups to the tenant schema
            kill_switch: Optional stateful-feature kill-switch (ADR-0024). When it reads disabled,
                stateful transforms are not called at all and their slots take their neutral value.
                Absent (None), stateful features compute — the switch is a control, not a
                requirement, so an engine constructed without one behaves as it always did.
        """
        self.base_sensors = feature_config.get('base_sensors', [])
        self.transformations = feature_config.get('transformations', [])
        self.feature_names = [t['name'] for t in self.transformations]
        # Every sensor the configuration reads: its declared inputs and any a feature names. A
        # reading lacking one is refused before a slot is filled (ADR-0044 dec 1).
        named = []
        for t in self.transformations:
            named += [t['sensor']] if t.get('sensor') else []
            named += [s for s in (t.get('sensors') or []) if s]
        self.required_sensors: List[str] = list(dict.fromkeys([*self.base_sensors, *named]))
        # A type no registered transform implements has no value to compute: refused here, before any
        # slot is filled, rather than scored on a substitute the model never saw.
        unknown = sorted({str(t.get('type')) for t in self.transformations if get_transform(t.get('type')) is None})
        if unknown:
            raise UnknownTransform(unknown)
        self.baseline_provider = baseline_provider
        self.equipment_id = equipment_id
        self.company_id = company_id
        self.kill_switch = kill_switch
        # Slots the switch neutralized on the last transform() call, and slots whose aggregate was
        # unavailable. Empty is the normal state, so a caller that reads them before transform()
        # sees "nothing degraded" rather than an error.
        self.neutralized_features: List[str] = []
        self.unavailable_features: List[str] = []

        logger.info(f"Initialized FeatureEngineeringEngine: {len(self.base_sensors)} base sensors, {len(self.transformations)} transformations, baseline provider: {baseline_provider is not None}")

    async def transform(self, sensor_data: Dict[str, float]) -> np.ndarray:
        """
        Apply all transformations to raw sensor data

        Args:
            sensor_data: Dictionary mapping sensor names to values
                Example: {"temp_inlet": 72.5, "pressure_1": 4.2, ...}

        Returns:
            numpy array of engineered features in the order defined by transformations

        Also sets ``neutralized_features`` (slots the kill-switch filled with a neutral value) and
        ``unavailable_features`` (stateful slots whose aggregate could not be read). The caller
        surfaces both, because a score on partially-neutral input must not look like an ordinary
        one (ADR-0024 dec 5, ADR-0044 dec 3).

        Raises ``MissingSensors`` for an incomplete reading and ``FeatureRefused`` for a definition
        that cannot be computed (ADR-0044 dec 1, 2); any other failure propagates (dec 4).
        """
        missing = missing_sensors(sensor_data, self.required_sensors)
        if missing:
            raise MissingSensors(missing)

        features = []
        neutralized: List[str] = []
        unavailable: List[str] = []

        # Read the switch ONCE per inference, not once per feature: a model may carry many stateful
        # features, and the switch's own read must not become N queries on the hot path — the load
        # it exists to relieve.
        stateful_enabled = True
        if self.kill_switch is not None:
            stateful_enabled = await self.kill_switch.enabled()

        for transformation in self.transformations:
            try:
                feature_value = await self._apply_transformation(
                    transformation, sensor_data, stateful_enabled, neutralized,
                )
            except StatefulUnavailable as e:
                # The outage fallback ADR-0024 keeps, for the stateful class only; from any other
                # transform this is an ordinary failure and fails the score (ADR-0044 dec 3, 4).
                if not is_stateful(transformation['type']):
                    raise
                logger.warning("Feature '%s' served neutral, aggregate unavailable: %s", transformation['name'], e)
                unavailable.append(transformation['name'])
                feature_value = neutral_value(transformation['type'])
            features.append(feature_value)

        self.neutralized_features = neutralized
        self.unavailable_features = unavailable
        if neutralized:
            logger.warning(
                "Stateful features neutralized by the kill-switch (ADR-0024): %d of %d slots (%s)",
                len(neutralized), len(self.transformations), ", ".join(neutralized[:5]),
            )

        return np.array(features).reshape(1, -1)

    async def _apply_transformation(self, transformation: Dict[str, Any], sensor_data: Dict[str, float],
                                    stateful_enabled: bool = True,
                                    neutralized: Optional[List[str]] = None) -> float:
        """
        Apply a single transformation by dispatching its ``type`` through the extension
        registry (ADR-0010). Built-in generic transforms and any loaded domain transforms
        are resolved the same way; the engine holds no transform-type knowledge itself.

        When the kill-switch is off, a stateful type short-circuits to its neutral value **here,
        ahead of the call** (ADR-0024 dec 4) — so a killed class issues zero substrate queries.
        Nulling the result afterwards would protect the model but keep hammering the degraded
        store, which is useless in the incident this is built for.
        """
        t_type = transformation['type']
        fn = get_transform(t_type)

        if not stateful_enabled and is_stateful(t_type):
            if neutralized is not None:
                neutralized.append(transformation['name'])
            return neutral_value(t_type)

        ctx = TransformContext(
            baseline_provider=self.baseline_provider,
            equipment_id=self.equipment_id,
            company_id=self.company_id,
        )
        return await fn(transformation, sensor_data, ctx)

    def get_feature_names(self) -> List[str]:
        """Get ordered list of feature names"""
        return self.feature_names
