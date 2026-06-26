# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Reference extension (ADR-0010 dec 5): a Tennessee-Eastman-Process domain transform that
registers through the platform's feature-transform contract WITHOUT editing the core.

This is illustrative — IndustryGrow is the real reference extension in its own repository.
The platform loads this module only when EXTENSION_MODULES names ``tep_reference``; the core
never imports it.
"""
from extensions import register_transform, register_detector, DetectionResult, check_api_version

# Declare the platform extension-API this module was written against (ADR-0010 dec 3).
EXTENSION_TARGET_API = "0.1.0"
check_api_version(EXTENSION_TARGET_API)


@register_transform("tep_reactor_pressure_margin")
async def reactor_pressure_margin(transformation, sensor_data, ctx):
    """
    Domain feature: how far reactor pressure sits below its TEP safety limit.

    A small margin means the reactor is running close to its pressure ceiling — a
    domain-meaningful signal that the generic transforms cannot express. The limit and the
    sensor are read from the feature configuration, not hardcoded into the platform.
    """
    params = transformation.get("params", {})
    limit_kpa = params.get("limit_kpa", 2950.0)
    sensor = transformation.get("sensor", "xmeas_7")  # TEP reactor pressure (kPa)
    return limit_kpa - sensor_data.get(sensor, 0.0)


def _flatten(features):
    """Flatten a (1, n) numpy array or a plain nested list without requiring numpy here."""
    flat = getattr(features, "flatten", None)
    if callable(flat):
        return list(features.flatten())
    if features and isinstance(features[0], (list, tuple)):
        return [v for row in features for v in row]
    return list(features or [])


@register_detector("tep_rule")
async def tep_rule_detector(features, model, threshold, ctx) -> DetectionResult:
    """
    Illustrative model-free domain detector (ADR-0010, detector contract): score from the
    largest engineered-feature magnitude against a domain scale, no trained model needed.
    A real domain detector would encode the operating envelope; this proves a domain can
    register a detector through the contract without touching the core scoring path.
    """
    values = [abs(float(v)) for v in _flatten(features)]
    scale = 100.0
    score = max(0.0, min(1.0, (max(values) / scale) if values else 0.0))
    return DetectionResult(score=score, is_anomaly=score >= threshold)
