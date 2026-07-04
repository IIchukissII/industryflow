# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Model-drift primitives (ADR-0021).

Pure, dependency-light helpers shared by the training/registration path (reference
profile capture) and the ml-service drift endpoint (score computation). Kept free of
FastAPI/DB so they are unit-testable in isolation.
"""

from .reference_profile import (
    REFERENCE_PROFILE_VERSION,
    build_reference_profile,
)
from .score import (
    DEFAULT_DRIFT_SHARE_THRESHOLD,
    MIN_ROWS,
    compute_drift,
)

__all__ = [
    "REFERENCE_PROFILE_VERSION",
    "build_reference_profile",
    "DEFAULT_DRIFT_SHARE_THRESHOLD",
    "MIN_ROWS",
    "compute_drift",
]
