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
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, List

logger = logging.getLogger(__name__)

# Extension-API version (semver). A major bump is a breaking contract change, never silent
# (ADR-0010 dec 3 / ADR-0000 supersession discipline).
EXTENSION_API_VERSION = "0.1.0"


@dataclass
class TransformContext:
    """Runtime services a feature transform may use (e.g. the rolling-window feature store)."""
    feature_store: Any = None
    equipment_id: Any = None


# A transform is: async (transformation_config, sensor_data, ctx) -> float
TransformFn = Callable[[Dict[str, Any], Dict[str, float], TransformContext], Awaitable[float]]

_TRANSFORMS: Dict[str, TransformFn] = {}


def register_transform(name: str):
    """Register a feature transform under ``name``. Re-registering a different function is an
    error rather than a silent overwrite (ADR-0010 negative consequence)."""
    def decorator(fn: TransformFn) -> TransformFn:
        existing = _TRANSFORMS.get(name)
        if existing is not None and existing is not fn:
            raise ValueError(f"feature transform '{name}' is already registered")
        _TRANSFORMS[name] = fn
        return fn
    return decorator


def get_transform(name: str):
    return _TRANSFORMS.get(name)


def registered_transforms() -> List[str]:
    return sorted(_TRANSFORMS)


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


# Register the platform's own generic transforms (no domain knowledge) on import.
from . import builtins as _builtins  # noqa: E402,F401
