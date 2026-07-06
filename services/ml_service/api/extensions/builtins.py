# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Generic feature transforms shipped with the platform (ADR-0010 dec 2).

These carry no domain knowledge — they are the building blocks any domain composes via
configuration. Domain-specific transforms live in extensions, registered the same way.
"""
import logging

from . import register_transform

logger = logging.getLogger(__name__)


@register_transform("identity")
async def identity(transformation, sensor_data, ctx):
    """Pass a sensor value through unchanged."""
    return sensor_data.get(transformation["sensor"], 0.0)


@register_transform("polynomial")
async def polynomial(transformation, sensor_data, ctx):
    """Raise a sensor value to a power."""
    power = transformation.get("params", {}).get("power", 2)
    return sensor_data.get(transformation["sensor"], 0.0) ** power


@register_transform("interaction")
async def interaction(transformation, sensor_data, ctx):
    """Combine two sensors: ratio, difference, product, or sum."""
    sensors = transformation["sensors"]
    operation = transformation.get("params", {}).get("operation", "product")
    if len(sensors) < 2:
        logger.warning("Interaction requires at least 2 sensors, got %d", len(sensors))
        return 0.0
    a = sensor_data.get(sensors[0], 0.0)
    b = sensor_data.get(sensors[1], 0.0)
    if operation == "ratio":
        return a / (b + 1e-8)
    if operation == "difference":
        return a - b
    if operation == "product":
        return a * b
    if operation == "sum":
        return a + b
    logger.warning("Unknown interaction operation: %s", operation)
    return 0.0


@register_transform("deviation")
async def deviation(transformation, sensor_data, ctx):
    """Difference of a sensor value from a fixed baseline."""
    baseline = transformation.get("params", {}).get("baseline", 0.0)
    return sensor_data.get(transformation["sensor"], 0.0) - baseline


@register_transform("statistical")
async def statistical(transformation, sensor_data, ctx):
    """Deviation of a sensor from its recent windowed mean.

    The mean is the Spark-materialized aggregate baseline (ADR-0023), read via the context's
    ``baseline_provider``. When it is unavailable — no aggregate row yet, or the read fails — the
    neutral value ``0.0`` ("no deviation from the mean") is returned, never the raw sensor value,
    which would be an out-of-distribution number in a slot the model expects to hold a small
    deviation, producing spurious anomaly scores.
    """
    params = transformation.get("params", {})
    stat_type = params.get("stat_type", "deviation_from_run_mean")
    sensor = transformation.get("sensor")
    if not sensor:
        logger.warning("Statistical feature '%s' missing sensor field", transformation.get("name"))
        return 0.0
    if stat_type != "deviation_from_run_mean":
        logger.warning("Unknown statistical type: %s", stat_type)
        return 0.0

    value = sensor_data.get(sensor, 0.0)
    # Which aggregate window is the baseline is domain configuration (ADR-0001), not a platform
    # constant: read it from params and let the provider apply its default.
    granularity = params.get("granularity")
    provider = ctx.baseline_provider
    if not (provider and ctx.equipment_id and ctx.company_id):
        return 0.0
    try:
        mean = await provider.compute_rolling_mean(
            company_id=ctx.company_id, equipment_id=ctx.equipment_id,
            sensor_name=sensor, granularity=granularity,
        )
    except Exception as e:
        logger.warning("Baseline unavailable for %s; using neutral 0.0: %s", sensor, e)
        return 0.0
    if mean is None:
        return 0.0
    return value - mean


@register_transform("rolling_stat")
async def rolling_stat(transformation, sensor_data, ctx):
    """Placeholder for rolling statistics not yet implemented."""
    logger.warning("Rolling statistics not yet implemented for feature '%s'", transformation.get("name"))
    return 0.0
