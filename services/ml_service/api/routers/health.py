"""
Health Check Router
Basic health and status endpoints
"""
from fastapi import APIRouter
from datetime import datetime
from pathlib import Path
import os

router = APIRouter(tags=["Health"])

MODEL_DIR = Path(os.getenv('MODEL_DIR', '/app/models'))


@router.get("/")
async def root():
    """API information"""
    return {
        "service": "IndustryFlow MLOps API",
        "version": "2.0.0",
        "architecture": "schema-per-tenant",
        "endpoints": {
            "docs": "/docs",
            "health": "/health",
            "models": "/api/models",
            "mlflow": "/api/mlflow"
        }
    }


@router.get("/health")
async def health_check():
    """
    Health check endpoint
    Returns service status and basic statistics
    """
    return {
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "model_dir": str(MODEL_DIR),
        "model_count": len(list(MODEL_DIR.glob("*.pkl"))),
        "mlflow_uri": os.getenv('MLFLOW_TRACKING_URI', 'not_configured')
    }
