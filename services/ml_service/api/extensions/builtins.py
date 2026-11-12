# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Generic feature transforms shipped with the platform (ADR-0010 dec 2).

These carry no domain knowledge — they are the building blocks any domain composes via
configuration. Domain-specific transforms live in extensions, registered the same way.
"""
import logging
import math

from industryflow_contracts import windows

from . import InvalidFeature, StatefulUnavailable, register_transform, sensor_value

logger = logging.getLogger(__name__)


def _sensor(transformation) -> str:
    """The sensor a single-sensor feature names; a definition that names none is refused."""
    sensor = transformation.get("sensor")
    if not sensor:
        raise InvalidFeature(transformation.get("name"), "it names no sensor")
    return sensor


def _params(transformation) -> dict:
    """The definition's parameters; a ``params`` that is not an object cannot be interpreted."""
    params = transformation.get("params")
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise InvalidFeature(transformation.get("name"), "its params are not an object")
    return params


def _number(transformation, param: str) -> float:
    """A finite numeric parameter the definition must name; absent or not a finite number is refused,
    never replaced by a default (ADR-0044 dec 2)."""
    value = _params(transformation).get(param)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise InvalidFeature(transformation.get("name"), f"it names no finite numeric params.{param}")
    return value


def _check_sensor(transformation) -> None:
    _sensor(transformation)


@register_transform("identity", check=_check_sensor)
async def identity(transformation, sensor_data, ctx):
    """Pass a sensor value through unchanged."""
    return sensor_value(sensor_data, _sensor(transformation))


def _check_polynomial(transformation) -> None:
    _sensor(transformation)
    _number(transformation, "power")


@register_transform("polynomial", check=_check_polynomial)
async def polynomial(transformation, sensor_data, ctx):
    """Raise a sensor value to ``params.power``."""
    _check_polynomial(transformation)
    return sensor_value(sensor_data, transformation["sensor"]) ** transformation["params"]["power"]


_OPERATIONS = ("ratio", "difference", "product", "sum")


def _check_interaction(transformation) -> None:
    sensors = transformation.get("sensors")
    if not isinstance(sensors, list) or len(sensors) != 2 or not all(isinstance(s, str) and s for s in sensors):
        raise InvalidFeature(transformation.get("name"), "an interaction names exactly 2 sensors")
    operation = _params(transformation).get("operation")
    if not isinstance(operation, str) or operation not in _OPERATIONS:
        raise InvalidFeature(transformation.get("name"),
                             f"params.operation {operation!r} is not one of {', '.join(_OPERATIONS)}")


@register_transform("interaction", check=_check_interaction)
async def interaction(transformation, sensor_data, ctx):
    """Combine two sensors by ``params.operation``: ratio, difference, product or sum."""
    _check_interaction(transformation)
    first, second = transformation["sensors"]
    operation = transformation["params"]["operation"]
    a, b = sensor_value(sensor_data, first), sensor_value(sensor_data, second)
    if operation == "ratio":
        if b == 0:
            # The quotient does not exist; a nudged denominator would serve a huge number as an
            # ordinary feature (ADR-0044 dec 4).
            raise ZeroDivisionError(f"feature '{transformation.get('name')}': ratio of {first} to {second}, which is 0")
        return a / b
    if operation == "difference":
        return a - b
    if operation == "product":
        return a * b
    return a + b


def _check_deviation(transformation) -> None:
    _sensor(transformation)
    _number(transformation, "baseline")


@register_transform("deviation", check=_check_deviation)
async def deviation(transformation, sensor_data, ctx):
    """Difference of a sensor value from ``params.baseline``."""
    _check_deviation(transformation)
    return sensor_value(sensor_data, transformation["sensor"]) - transformation["params"]["baseline"]


# The statistics a `statistical` feature can carry, all sourced from the same closed window of the
# Spark-materialized aggregates (ADR-0023 dec 7, 9). `deviation_from_run_mean` is the legacy name
# for the deviation: it never meant a "run" at serve time, and is accepted only as a deprecated
# alias so existing feature configs keep resolving (ADR-0010 — no silent contract break).
_LEGACY_STAT_TYPES = {"deviation_from_run_mean": "deviation_from_window_mean"}
_STAT_TYPES = ("deviation_from_window_mean", "window_mean", "window_std")


def _stat_type(transformation) -> str:
    """The definition's statistic, the legacy alias resolved; none, or an unknown one, is refused —
    there is no default statistic (ADR-0023 dec 9, ADR-0044 dec 2)."""
    stat_type = _params(transformation).get("stat_type")
    if isinstance(stat_type, str) and stat_type in _LEGACY_STAT_TYPES:
        return _LEGACY_STAT_TYPES[stat_type]
    if not isinstance(stat_type, str) or stat_type not in _STAT_TYPES:
        raise InvalidFeature(transformation.get("name"),
                             f"stat_type {stat_type!r} is not one of {', '.join(_STAT_TYPES)}")
    return stat_type


def _check_statistical(transformation) -> None:
    _sensor(transformation)
    _stat_type(transformation)
    # Which aggregate window is the baseline is domain configuration (ADR-0001), named by the
    # definition: one of the windows the shared contract declares, with no default (ADR-0044 dec 2).
    granularity = _params(transformation).get("granularity")
    if not isinstance(granularity, str) or granularity not in windows.WINDOWS:
        raise InvalidFeature(transformation.get("name"),
                             f"params.granularity {granularity!r} is not one of {', '.join(windows.WINDOWS)}")


@register_transform("statistical", check=_check_statistical, stateful=True)
async def statistical(transformation, sensor_data, ctx):
    """A sensor's statistic over its most recent closed aggregate window.

    ``stat_type`` selects which one: ``deviation_from_window_mean`` (value − mean),
    ``window_mean``, or ``window_std``. All three read the same Spark-materialized baseline
    (ADR-0023) via the context's ``baseline_provider``, at the window named by ``granularity`` —
    the same window the offline training re-derivation uses, which is what keeps train and serve
    computing one quantity rather than two (ADR-0023 dec 9).

    When the store cannot supply the value — no provider, the store out, or no closed window yet —
    it raises ``StatefulUnavailable``: the engine fills the neutral value and the response names the
    slot (ADR-0024 dec 3, 5; ADR-0044 dec 3). Every written window carries its spread, zero for one
    reading (ADR-0006 dec 9), so a window without one is a defect and fails the score (dec 4). Never the raw sensor value,
    which would be an out-of-distribution number in a slot the model expects to hold a small
    deviation.
    """
    _check_statistical(transformation)
    sensor = transformation["sensor"]
    stat_type = _stat_type(transformation)
    if transformation["params"].get("stat_type") != stat_type:
        logger.warning("Feature '%s' uses deprecated stat_type '%s'; use '%s' (ADR-0023 dec 9)",
                       transformation.get("name"), transformation["params"]["stat_type"], stat_type)
    # The reading is checked before the aggregate, so an incomplete one is refused rather than
    # reported as a degraded store.
    current = sensor_value(sensor_data, sensor) if stat_type == "deviation_from_window_mean" else None
    provider = ctx.baseline_provider
    if not provider:
        raise StatefulUnavailable(f"no baseline source for {sensor}")
    # The provider answers None when its store cannot supply the baseline; anything it raises is a
    # defect, not an outage, and is not served neutral (ADR-0044 dec 3, 4).
    baseline = await provider.compute_baseline(
        company_id=ctx.company_id, equipment_id=ctx.equipment_id,
        sensor_name=sensor, granularity=transformation["params"]["granularity"],
    )
    if baseline is None:
        raise StatefulUnavailable(f"no closed aggregate window yet for {sensor}")

    if stat_type == "window_mean":
        return baseline["mean"]
    if stat_type == "window_std":
        if baseline["std"] is None:
            raise RuntimeError(f"the latest window of {sensor} carries no spread (ADR-0006 dec 9)")
        return baseline["std"]
    return current - baseline["mean"]


def _not_implemented(transformation) -> None:
    raise InvalidFeature(transformation.get("name"), "rolling statistics are not implemented")


@register_transform("rolling_stat", check=_not_implemented, stateful=True)
async def rolling_stat(transformation, sensor_data, ctx):
    """Placeholder for rolling statistics not yet implemented.

    Registered ``stateful=True`` (ADR-0024 dec 1) because of what it *will* read, not what it reads
    today: it belongs to the class the kill-switch governs, and tagging it on the day it is
    implemented would be a thing to forget.
    """
    _not_implemented(transformation)
