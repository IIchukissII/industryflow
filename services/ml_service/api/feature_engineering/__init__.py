# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Feature Engineering Module
Flexible, configuration-driven feature engineering for multiple equipment types
"""
from .baseline_provider import AggregateBaselineProvider
from .engine import FeatureEngineeringEngine

__all__ = ['AggregateBaselineProvider', 'FeatureEngineeringEngine']
