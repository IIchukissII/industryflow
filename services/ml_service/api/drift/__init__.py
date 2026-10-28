# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Model-drift primitives (ADR-0021).

Pure, dependency-light helpers shared by the training/registration path (reference
profile capture) and the ml-service drift endpoint (score computation). Kept free of
FastAPI/DB so they are unit-testable in isolation.
"""

from .reference_profile import (
    FEATURE_SAMPLE_ARTIFACT,
    MAX_SAMPLE_BYTES,
    REFERENCE_PROFILE_VERSION,
    accepted_sample,
    baseline_state,
    build_reference_profile,
    prediction_column_refusal,
    refused_baseline,
    sample_refusal,
    sample_rows,
    supplied_scores_refusal,
    with_derived_scores,
    without_prediction_baseline,
)
from .score import (
    DEFAULT_DRIFT_SHARE_THRESHOLD,
    MIN_ROWS,
    align_readings,
    compute_drift,
)

__all__ = [
    "FEATURE_SAMPLE_ARTIFACT",
    "MAX_SAMPLE_BYTES",
    "REFERENCE_PROFILE_VERSION",
    "accepted_sample",
    "baseline_state",
    "build_reference_profile",
    "prediction_column_refusal",
    "refused_baseline",
    "sample_refusal",
    "sample_rows",
    "supplied_scores_refusal",
    "with_derived_scores",
    "without_prediction_baseline",
    "DEFAULT_DRIFT_SHARE_THRESHOLD",
    "MIN_ROWS",
    "align_readings",
    "compute_drift",
]
