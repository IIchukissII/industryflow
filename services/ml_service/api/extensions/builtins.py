# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Generic feature transforms shipped with the platform (ADR-0010 dec 2).

These carry no domain knowledge — they are the building blocks any domain composes via
configuration. Domain-specific transforms live in extensions, registered the same way.
"""
import logging

from . import InvalidFeature, StatefulUnavailable, register_transform, sensor_value

logger = logging.getLogger(__name__)


def _sensor(transformation) -> str:
    """The sensor a single-sensor feature names; a definition that names none is refused."""
    sensor = transformation.get("sensor")
    if not sensor:
        raise InvalidFeature(transformation.get("name"), "it names no sensor")
    return sensor


@register_transform("identity")
async def identity(transformation, sensor_data, ctx):
    """Pass a sensor value through unchanged."""
    return sensor_value(sensor_data, _sensor(transformation))


@register_transform("polynomial")
async def polynomial(transformation, sensor_data, ctx):
    """Raise a sensor value to a power."""
    power = transformation.get("params", {}).get("power", 2)
    return sensor_value(sensor_data, _sensor(transformation)) ** power


@register_transform("interaction")
async def interaction(transformation, sensor_data, ctx):
    """Combine two sensors: ratio, difference, product, or sum."""
    sensors = transformation.get("sensors") or []
    operation = transformation.get("params", {}).get("operation", "product")
    if len(sensors) < 2:
        raise InvalidFeature(transformation.get("name"), f"an interaction needs 2 sensors, it names {len(sensors)}")
    a = sensor_value(sensor_data, sensors[0])
    b = sensor_value(sensor_data, sensors[1])
    if operation == "ratio":
        return a / (b + 1e-8)
    if operation == "difference":
        return a - b
    if operation == "product":
        return a * b
    if operation == "sum":
        return a + b
    raise InvalidFeature(transformation.get("name"), f"unknown interaction operation {operation!r}")


@register_transform("deviation")
async def deviation(transformation, sensor_data, ctx):
    """Difference of a sensor value from a fixed baseline."""
    baseline = transformation.get("params", {}).get("baseline", 0.0)
    return sensor_value(sensor_data, _sensor(transformation)) - baseline


# The statistics a `statistical` feature can carry, all sourced from the same closed window of the
# Spark-materialized aggregates (ADR-0023 dec 7, 9). `deviation_from_run_mean` is the legacy name
# for the deviation: it never meant a "run" at serve time, and is accepted only as a deprecated
# alias so existing feature configs keep resolving (ADR-0010 — no silent contract break).
_LEGACY_STAT_TYPES = {"deviation_from_run_mean": "deviation_from_window_mean"}
_STAT_TYPES = ("deviation_from_window_mean", "window_mean", "window_std")


@register_transform("statistical", stateful=True)
async def statistical(transformation, sensor_data, ctx):
    """A sensor's statistic over its most recent closed aggregate window.

    ``stat_type`` selects which one: ``deviation_from_window_mean`` (value − mean),
    ``window_mean``, or ``window_std``. All three read the same Spark-materialized baseline
    (ADR-0023) via the context's ``baseline_provider``, at the window named by ``granularity`` —
    the same window the offline training re-derivation uses, which is what keeps train and serve
    computing one quantity rather than two (ADR-0023 dec 9).

    When the baseline is unavailable — no aggregate row yet, no provider, or the read fails — it
    raises ``StatefulUnavailable``: the engine fills the neutral value and the response names the
    slot (ADR-0024 dec 3, 5; ADR-0044 dec 3). Never the raw sensor value, which would be an
    out-of-distribution number in a slot the model expects to hold a small deviation.

    ``stat_type`` is required: there is no default. A missing or unknown one is refused
    (ADR-0044 dec 2) rather than serving some other statistic, or a neutral, into the slot — the
    defect that made ``_run_mean`` and ``_run_std`` features receive a deviation.
    """
    params = transformation.get("params", {})
    sensor = _sensor(transformation)

    stat_type = params.get("stat_type")
    if stat_type in _LEGACY_STAT_TYPES:
        canonical = _LEGACY_STAT_TYPES[stat_type]
        logger.warning(
            "Feature '%s' uses deprecated stat_type '%s'; use '%s' (ADR-0023 dec 9)",
            transformation.get("name"), stat_type, canonical,
        )
        stat_type = canonical
    if stat_type not in _STAT_TYPES:
        raise InvalidFeature(
            transformation.get("name"),
            f"stat_type {stat_type!r} is not one of {', '.join(_STAT_TYPES)}",
        )

    # Which aggregate window is the baseline is domain configuration (ADR-0001), not a platform
    # constant: read it from params and let the provider apply its default.
    granularity = params.get("granularity")
    provider = ctx.baseline_provider
    # The reading is checked before the aggregate, so an incomplete one is refused rather than
    # reported as a degraded store.
    current = sensor_value(sensor_data, sensor) if stat_type == "deviation_from_window_mean" else None
    if not (provider and ctx.equipment_id and ctx.company_id):
        raise StatefulUnavailable(f"no baseline source for {sensor}")
    try:
        baseline = await provider.compute_baseline(
            company_id=ctx.company_id, equipment_id=ctx.equipment_id,
            sensor_name=sensor, granularity=granularity,
        )
    except Exception as e:
        raise StatefulUnavailable(f"baseline read failed for {sensor}: {e}") from e
    if baseline is None:
        raise StatefulUnavailable(f"no closed aggregate window yet for {sensor}")

    if stat_type == "window_mean":
        return baseline["mean"]
    if stat_type == "window_std":
        # A single-sample window has no stddev; 0.0 ("no spread") is the neutral there.
        return baseline["std"] if baseline["std"] is not None else 0.0
    return current - baseline["mean"]


@register_transform("rolling_stat", stateful=True)
async def rolling_stat(transformation, sensor_data, ctx):
    """Placeholder for rolling statistics not yet implemented.

    Registered ``stateful=True`` (ADR-0024 dec 1) because of what it *will* read, not what it reads
    today: it belongs to the class the kill-switch governs, and tagging it on the day it is
    implemented would be a thing to forget.
    """
    raise InvalidFeature(transformation.get("name"), "rolling statistics are not implemented")
