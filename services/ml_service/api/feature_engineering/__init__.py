# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Feature Engineering Module
Flexible, configuration-driven feature engineering for multiple equipment types
"""
from .baseline_provider import AggregateBaselineProvider
from extensions import FeatureRefused, MissingSensors

from .engine import FeatureEngineeringEngine, UnknownTransform
from .kill_switch import StatefulFeatureSwitch

__all__ = ['AggregateBaselineProvider', 'FeatureEngineeringEngine', 'FeatureRefused', 'MissingSensors',
           'StatefulFeatureSwitch', 'UnknownTransform']
