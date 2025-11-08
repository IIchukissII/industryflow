# src/api/routers/ingestion.py
"""
Secure sensor data ingestion API with authentication.
Prevents company_id spoofing by forcing authenticated user's company.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime
import logging

from schemas import SensorDataInput, SensorDataResponse
from dependencies import get_current_user_with_company
from messaging.kafka_producer import send_sensor_data
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/ingest",
    tags=["Data Ingestion"]
)


@router.post(
    "/sensor-data",
    response_model=SensorDataResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest sensor data (authenticated)",
    description="""
    Secure endpoint for sensor data ingestion.

    **Security:**
    - Requires JWT authentication
    - company_id is FORCED from authenticated user's token
    - Prevents cross-tenant data injection
    - Protects against DDoS attacks via fake company_id

    **Usage:**
    1. Login to get JWT token
    2. Include token in Authorization header
    3. Send sensor data (without company_id)
    4. System automatically assigns your company_id
    """
)
async def ingest_sensor_data(
        data: SensorDataInput,
        current_user: User = Depends(get_current_user_with_company)
) -> SensorDataResponse:
    """
    Ingest authenticated sensor data (OPTIMIZED).

    The company_id is AUTOMATICALLY set from the authenticated user's JWT token.
    This prevents malicious actors from injecting data into other companies.
    """
    try:
        # OPTIMIZATION: Pre-convert company_id once
        company_id_str = str(current_user.company_id)

        # OPTIMIZATION: Build message inline without intermediate variable
        success = await send_sensor_data({
            "timestamp": data.timestamp.isoformat(),
            "sensor_id": data.sensor_id,
            "equipment_id": data.equipment_id,
            "site_id": data.site_id,
            "company_id": company_id_str,
            "value": data.value,
            "unit": data.unit,
            "quality_code": data.quality_code if data.quality_code is not None else 0
        })

        # OPTIMIZATION: Fail fast without raising exception for better throughput
        if not success:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to queue message to Kafka"
            )

        # OPTIMIZATION: Removed per-message logging (only errors logged)
        # logger.info removed for performance

        # OPTIMIZATION: Simplified response
        return SensorDataResponse(
            status="accepted",
            message="Sensor data queued for processing",
            company_id=company_id_str,
            sensor_id=data.sensor_id,
            timestamp=data.timestamp
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Ingestion error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )


@router.get(
    "/stats",
    summary="Get ingestion statistics (authenticated)",
    description="Returns statistics about data ingestion for the authenticated company"
)
async def get_ingestion_stats(
        current_user: User = Depends(get_current_user_with_company)
):
    """
    Get ingestion statistics for authenticated company.
    Future: Can track rate limits, message counts, etc.
    """
    company_id_str = str(current_user.company_id)

    return {
        "company_id": company_id_str,
        "user_email": current_user.email,
        "message": "Ingestion statistics endpoint - to be implemented",
        "status": "active"
    }