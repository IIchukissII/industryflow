# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Jupyter Notebook Utilities for IndustryFlow ML Service

This package provides helper utilities for data scientists working in Jupyter notebooks.
"""

from .feature_config_builder import (
    FeatureConfigBuilder,
    load_config,
    batch_add_identities,
    batch_add_polynomials,
    batch_add_interactions
)
from .offline_baseline import (
    add_window_features,
    feature_config,
    window_index,
    GRANULARITY_SECONDS
)

__all__ = [
    'FeatureConfigBuilder',
    'load_config',
    'batch_add_identities',
    'batch_add_polynomials',
    'batch_add_interactions',
    'add_window_features',
    'feature_config',
    'window_index',
    'GRANULARITY_SECONDS'
]

__version__ = '1.0.0'
