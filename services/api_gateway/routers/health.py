# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

from fastapi import APIRouter
from datetime import datetime
from models import HealthCheck
from database import test_connection

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("", response_model=HealthCheck)
async def health_check():
    """Health check endpoint - verify API and database connectivity"""
    try:
        db_connected = await test_connection()
        db_status = "connected" if db_connected else "disconnected"
    except Exception as e:
        db_status = f"error: {str(e)}"

    return HealthCheck(
        status="healthy" if db_status == "connected" else "unhealthy",
        database=db_status,
        timestamp=datetime.now()
    )