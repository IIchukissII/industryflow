# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
IndustryFlow extension SDK (ADR-0008 / ADR-0010).

The platform exposes stable, versioned plugin contracts; domains implement against them and
never edit core paths. The first contract is the feature transform: a domain registers a new
transform *type* in its own module, and the engine dispatches through this registry instead of
a hardcoded branch. The core imports only its own generic built-ins plus whatever modules the
operator names in EXTENSION_MODULES — never a named extension directly.
"""
import importlib
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List

logger = logging.getLogger(__name__)

# Extension-API version (semver). A major bump is a breaking contract change, never silent
# (ADR-0010 dec 3 / ADR-0000 supersession discipline). 0.2.0 adds the optional `stateful` /
# `neutral` capability tags to register_transform (ADR-0024 dec 1) — additive, with
# back-compatible defaults, so transforms written against 0.1.0 keep working untouched.
EXTENSION_API_VERSION = "0.2.0"


@dataclass
class TransformContext:
    """Runtime services a feature transform may use.

    ``baseline_provider`` reads a sensor's windowed baseline (mean/std) from the
    Spark-materialized aggregates (ADR-0023); ``company_id`` scopes that read to the tenant
    schema, ``equipment_id`` to the equipment.
    """
    baseline_provider: Any = None
    equipment_id: Any = None
    company_id: Any = None


# A transform is: async (transformation_config, sensor_data, ctx) -> float
TransformFn = Callable[[Dict[str, Any], Dict[str, float], TransformContext], Awaitable[float]]

_TRANSFORMS: Dict[str, TransformFn] = {}
# Capability tags per transform type (ADR-0024 dec 1): {"stateful": bool, "neutral": float}.
# The engine dispatches by type and holds no transform knowledge (ADR-0010), so the *transform*
# declares whether it reads an external substrate — that is what lets the kill-switch neutralize a
# whole class without the engine hardcoding which types belong to it.
_TRANSFORM_TAGS: Dict[str, Dict[str, Any]] = {}


def register_transform(name: str, *, stateful: bool = False, neutral: float = 0.0):
    """Register a feature transform under ``name``. Re-registering a different function is an
    error rather than a silent overwrite (ADR-0010 negative consequence).

    ``stateful=True`` declares that the transform reads an external substrate (the aggregate
    baseline) rather than being a pure function of the current reading. The kill-switch (ADR-0024)
    neutralizes exactly this class: when it is off, the engine fills the slot with ``neutral``
    *without calling the transform*, so a degraded substrate stops being queried.

    ``neutral`` is the value that slot takes when the class is killed — ``0.0`` ("no deviation")
    for the deviation-style features this ships with. Both keywords are optional with
    back-compatible defaults, so this is an additive contract change (a minor extension-API bump,
    ADR-0010 dec 3); transforms that pass neither keep working unchanged.
    """
    def decorator(fn: TransformFn) -> TransformFn:
        existing = _TRANSFORMS.get(name)
        if existing is not None and existing is not fn:
            raise ValueError(f"feature transform '{name}' is already registered")
        _TRANSFORMS[name] = fn
        _TRANSFORM_TAGS[name] = {"stateful": stateful, "neutral": neutral}
        return fn
    return decorator


def get_transform(name: str):
    return _TRANSFORMS.get(name)


def is_stateful(name: str) -> bool:
    """Whether a transform type reads an external substrate. Unknown types are not stateful — an
    unregistered type is never dispatched anyway, and guessing 'stateful' would let a typo in a
    config silently neutralize a feature."""
    return _TRANSFORM_TAGS.get(name, {}).get("stateful", False)


def neutral_value(name: str) -> float:
    """The value a killed transform's slot takes (ADR-0024 dec 3)."""
    return _TRANSFORM_TAGS.get(name, {}).get("neutral", 0.0)


def registered_transforms() -> List[str]:
    return sorted(_TRANSFORMS)


def stateful_transforms() -> List[str]:
    return sorted(n for n, tags in _TRANSFORM_TAGS.items() if tags.get("stateful"))


# --- anomaly-detector contract (ADR-0010: the second contract on the registry pattern) ---

@dataclass
class DetectorContext:
    """Context an anomaly detector may use (feature names, the equipment under inference)."""
    feature_names: Any = None
    equipment_id: Any = None


@dataclass
class DetectionResult:
    """A detector's verdict: a 0–1 anomaly score and whether it crosses the threshold."""
    score: float
    is_anomaly: bool
    detail: Dict[str, Any] = field(default_factory=dict)


# A detector is: async (features, model, threshold, ctx) -> DetectionResult. `model` is the
# loaded model (or None for model-free detectors); a detector reads only what it needs.
DetectorFn = Callable[[Any, Any, float, DetectorContext], Awaitable[DetectionResult]]

_DETECTORS: Dict[str, DetectorFn] = {}


def register_detector(name: str):
    """Register an anomaly detector under ``name`` (ADR-0010). Re-registering a different
    function is an error, mirroring transforms."""
    def decorator(fn: DetectorFn) -> DetectorFn:
        existing = _DETECTORS.get(name)
        if existing is not None and existing is not fn:
            raise ValueError(f"anomaly detector '{name}' is already registered")
        _DETECTORS[name] = fn
        return fn
    return decorator


def get_detector(name: str):
    return _DETECTORS.get(name)


def registered_detectors() -> List[str]:
    return sorted(_DETECTORS)


def check_api_version(target: str) -> None:
    """Accept an extension targeting ``target`` when the major matches and the platform minor is
    at least the target's; refuse otherwise (ADR-0010 dec 3)."""
    try:
        t_major, t_minor = (int(part) for part in target.split(".")[:2])
        p_major, p_minor = (int(part) for part in EXTENSION_API_VERSION.split(".")[:2])
    except (ValueError, AttributeError):
        raise ValueError(f"invalid extension api version: {target!r}")
    if t_major != p_major or t_minor > p_minor:
        raise ValueError(
            f"extension targets extension-api {target}, but the platform provides "
            f"{EXTENSION_API_VERSION}"
        )


def load_extension_modules(modules: List[str]) -> List[str]:
    """Import each named module so its ``@register_*`` decorators run (ADR-0010 dec 1). The
    platform never imports a named extension itself — only what it is configured to load."""
    loaded: List[str] = []
    for raw in modules:
        name = raw.strip()
        if not name:
            continue
        importlib.import_module(name)
        loaded.append(name)
        logger.info("Loaded extension module: %s", name)
    return loaded


# Register the platform's own generic transforms + detectors (no domain knowledge) on import.
from . import builtins as _builtins  # noqa: E402,F401
from . import builtins_detectors as _builtins_detectors  # noqa: E402,F401
