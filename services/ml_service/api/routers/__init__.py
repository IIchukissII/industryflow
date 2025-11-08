"""
API Routers for ML Service
"""
from .health import router as health_router
from .models import router as models_router
from .mlflow import router as mlflow_router

__all__ = ['health_router', 'models_router', 'mlflow_router']
