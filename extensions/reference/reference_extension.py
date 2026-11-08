# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The reference extension (ADR-0010 dec 5, ADR-0008 dec 6): one domain transform and one model-free
detector, registered through the platform's contracts without editing the core.

The domain is a fictional pump. The platform imports this module only when EXTENSION_MODULES names
``reference_extension``; no platform module imports it.
"""
from extensions import (DIRECT_SCORE, DetectionResult, InvalidFeature, UninterpretableModel, check_api_version,
                        register_detector, register_transform, sensor_value)

# The extension API this module is written against (ADR-0010 dec 3).
check_api_version("0.5.0")

# The feature the detector scores, and the headroom below which it scores at all: the pump's own
# operating envelope, which belongs to this domain, not to the platform.
MARGIN_FEATURE = "pressure_margin"
HEADROOM_BAR = 2.0


@register_transform("reference_pressure_margin")
async def pressure_margin(transformation, sensor_data, ctx):
    """How far a pressure sensor sits below the limit the feature names: ``params.limit`` minus the
    reading. A definition naming no sensor or no numeric limit is refused (ADR-0044 dec 2)."""
    sensor = transformation.get("sensor")
    limit = (transformation.get("params") or {}).get("limit")
    if not sensor:
        raise InvalidFeature(transformation.get("name"), "it names no sensor")
    if isinstance(limit, bool) or not isinstance(limit, (int, float)):
        raise InvalidFeature(transformation.get("name"), "it names no numeric params.limit")
    return limit - sensor_value(sensor_data, sensor)


@register_detector("reference_margin_rule", semantics=DIRECT_SCORE)
async def margin_rule(features, model, threshold, ctx) -> DetectionResult:
    """Model-free: scores the pressure margin feature, 0 at ``HEADROOM_BAR`` or more, 1 at or past the
    limit, linear between. Features that carry no margin cannot be scored (ADR-0028 dec 2)."""
    names = list(ctx.feature_names or [])
    if MARGIN_FEATURE not in names:
        raise UninterpretableModel(f"the features carry no {MARGIN_FEATURE!r}")
    values = features.flatten() if hasattr(features, "flatten") else features
    margin = float(values[names.index(MARGIN_FEATURE)])
    score = min(1.0, max(0.0, 1.0 - margin / HEADROOM_BAR))
    return DetectionResult(score=score, is_anomaly=score >= threshold, detail={"margin": margin})
