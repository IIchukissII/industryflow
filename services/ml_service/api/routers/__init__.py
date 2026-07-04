# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
API Routers for ML Service
"""
from .health import router as health_router
from .models import router as models_router
from .registered_models import router as registered_models_router
from .inference import router as inference_router
from .feature_configs import router as feature_configs_router
from .drift import router as drift_router

__all__ = [
    'health_router', 'models_router', 'registered_models_router',
    'inference_router', 'feature_configs_router', 'drift_router',
]
