# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
What this deployment can actually serve (ADR-0028 dec 5).

The framework set a data scientist may author in is OPEN; what any given ml_service image can honour
is closed. ADR-0027 makes the mismatch honest — an artifact this environment cannot satisfy is refused
at the gate rather than mis-served — but a refusal at deploy time is a late place to learn a fact that
was knowable all along. This endpoint moves it earlier: **ask what a deployment will accept before you
train against it.**

It is DISCOVERED from the registry, never a hand-kept list. The registry already knows which detectors
are loaded (built-ins plus whatever the operator named in EXTENSION_MODULES) and what each declares it
can score. A second list, maintained by hand, would be a second source of truth — and would go stale
the first time an operator added an adapter, which is exactly the class of drift ADR-0000 exists to
prevent.
"""
import logging
from importlib.metadata import PackageNotFoundError, version as installed_version

from fastapi import APIRouter

from extensions import EXTENSION_API_VERSION, detector_capabilities, registered_transforms

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ml", tags=["Capabilities"])

# The artifact flavors this image could load if asked. Presence of the flavor's own library is what
# decides it — the same question ADR-0027's gate asks of an artifact's declared requirements, asked
# here of the environment itself, so the two answers cannot disagree.
_FLAVOR_LIBRARIES = {
    "mlflow.sklearn": "scikit-learn",
    "mlflow.xgboost": "xgboost",
    "mlflow.pytorch": "torch",
    "mlflow.keras": "keras",
    "mlflow.lightgbm": "lightgbm",
}


def _installed(package: str) -> str | None:
    try:
        return installed_version(package)
    except PackageNotFoundError:
        return None


@router.get("/capabilities")
async def get_capabilities():
    """What this ml_service can load, and what it knows how to interpret.

    Two different questions, and a model needs BOTH answered yes:
      - can this image LOAD the artifact (is the flavor's library here)?         -> ADR-0027
      - is there a detector that knows what the model's output MEANS?            -> ADR-0028
    A torch artifact fails the first; a model whose semantics nobody declared fails the second.
    """
    detectors = detector_capabilities()

    claimed = {f for d in detectors for f in d.get("handles_flavors", [])}

    servable, unservable = [], []
    for flavor, library in sorted(_FLAVOR_LIBRARIES.items()):
        present = _installed(library)
        entry = {
            "flavor": flavor,
            "library": library,
            "installed_version": present,
            # A flavor is only truly servable when the library is here AND something can score it.
            "scored_by": sorted(d["name"] for d in detectors if flavor in d.get("handles_flavors", [])),
        }
        (servable if present and entry["scored_by"] else unservable).append(entry)

    return {
        "extension_api_version": EXTENSION_API_VERSION,
        "detectors": detectors,
        "servable_flavors": servable,
        # Named, not hidden. An operator asking "why won't my torch model deploy" should find the
        # answer here rather than in a stack trace — and the answer for torch is that serving a new
        # framework needs an adapter, not a version bump (ADR-0028 dec 7).
        "unservable_flavors": unservable,
        "transforms": registered_transforms(),
        "unclaimed_flavors": sorted(claimed - set(_FLAVOR_LIBRARIES)),
    }
