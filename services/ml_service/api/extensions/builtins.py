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


# The statistics a `statistical` feature can carry, all sourced from the same closed window of the
# Spark-materialized aggregates (ADR-0023 rev 2). `deviation_from_run_mean` is the pre-rev-2 name
# for the deviation: it never meant a "run" at serve time, and is accepted only as a deprecated
# alias so existing feature configs keep resolving (ADR-0010 — no silent contract break).
_LEGACY_STAT_TYPES = {"deviation_from_run_mean": "deviation_from_window_mean"}
_STAT_TYPES = ("deviation_from_window_mean", "window_mean", "window_std")


@register_transform("statistical")
async def statistical(transformation, sensor_data, ctx):
    """A sensor's statistic over its most recent closed aggregate window.

    ``stat_type`` selects which one: ``deviation_from_window_mean`` (value − mean),
    ``window_mean``, or ``window_std``. All three read the same Spark-materialized baseline
    (ADR-0023) via the context's ``baseline_provider``, at the window named by ``granularity`` —
    the same window the offline training re-derivation uses, which is what keeps train and serve
    computing one quantity rather than two (ADR-0023 rev 2 dec 9).

    When the baseline is unavailable — no aggregate row yet, or the read fails — the neutral value
    ``0.0`` is returned, never the raw sensor value, which would be an out-of-distribution number
    in a slot the model expects to hold a small deviation, producing spurious anomaly scores.

    ``stat_type`` is required: there is no default. A missing or unknown one degrades to neutral
    rather than silently serving some other statistic into the feature's slot — the defect that
    made ``_run_mean`` and ``_run_std`` features receive a deviation.
    """
    params = transformation.get("params", {})
    sensor = transformation.get("sensor")
    if not sensor:
        logger.warning("Statistical feature '%s' missing sensor field", transformation.get("name"))
        return 0.0

    stat_type = params.get("stat_type")
    if stat_type in _LEGACY_STAT_TYPES:
        canonical = _LEGACY_STAT_TYPES[stat_type]
        logger.warning(
            "Feature '%s' uses deprecated stat_type '%s'; use '%s' (ADR-0023 rev 2)",
            transformation.get("name"), stat_type, canonical,
        )
        stat_type = canonical
    if stat_type not in _STAT_TYPES:
        logger.warning(
            "Statistical feature '%s' has missing/unknown stat_type %r (expected one of %s); "
            "using neutral 0.0", transformation.get("name"), stat_type, ", ".join(_STAT_TYPES),
        )
        return 0.0

    # Which aggregate window is the baseline is domain configuration (ADR-0001), not a platform
    # constant: read it from params and let the provider apply its default.
    granularity = params.get("granularity")
    provider = ctx.baseline_provider
    if not (provider and ctx.equipment_id and ctx.company_id):
        return 0.0
    try:
        baseline = await provider.compute_baseline(
            company_id=ctx.company_id, equipment_id=ctx.equipment_id,
            sensor_name=sensor, granularity=granularity,
        )
    except Exception as e:
        logger.warning("Baseline unavailable for %s; using neutral 0.0: %s", sensor, e)
        return 0.0
    if baseline is None:
        return 0.0

    if stat_type == "window_mean":
        return baseline["mean"]
    if stat_type == "window_std":
        # A single-sample window has no stddev; 0.0 ("no spread") is the neutral there.
        return baseline["std"] if baseline["std"] is not None else 0.0
    return sensor_data.get(sensor, 0.0) - baseline["mean"]


@register_transform("rolling_stat")
async def rolling_stat(transformation, sensor_data, ctx):
    """Placeholder for rolling statistics not yet implemented."""
    logger.warning("Rolling statistics not yet implemented for feature '%s'", transformation.get("name"))
    return 0.0
