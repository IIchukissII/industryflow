# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
IndustryFlow Ingestion Service
High-throughput sensor data ingestion endpoint (Port 8003)
"""
from fastapi import FastAPI, Depends, HTTPException, status
from prometheus_fastapi_instrumentator import Instrumentator
from aiokafka import AIOKafkaConsumer
from datetime import datetime, timezone
import asyncio
import logging

from config import get_settings
from schemas import SensorDataInput, SensorDataResponse
from dependencies import get_ingestion_identity
from kafka_producer import AsyncKafkaProducerSingleton, send_sensor_data
import tenant_status

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

settings = get_settings()

# The tenant-status follower (startup_event); cancelled on shutdown.
_status_task = None

# FastAPI app
app = FastAPI(
    title="IndustryFlow Ingestion Service",
    description="High-throughput sensor data ingestion endpoint",
    version="1.0.0"
)

# No CORS middleware: only devices call this service, over mTLS, never a browser (ADR-0002).

# Prometheus metrics at /metrics (request rate/latency/in-flight — item 7). Scraped in-cluster
# on the service port; the mTLS edge denies /metrics so it is never exposed to devices.
Instrumentator().instrument(app).expose(app)

@app.on_event("startup")
async def startup_event():
    """Initialize connections on startup"""
    logger.info("=" * 80)
    logger.info("🚀 Ingestion Service starting...")
    logger.info(f"Port: {settings.INGESTION_SERVICE_PORT}")
    logger.info(f"Kafka: {settings.KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Topic: {settings.KAFKA_TOPIC_SENSOR_DATA}")

    # Ingestion holds no database role; it only produces to Kafka (ADR-0002).
    logger.info("Initializing Kafka producer...")
    await AsyncKafkaProducerSingleton.get_producer()
    logger.info("✅ Kafka producer initialized")

    # Refuse suspended tenants from the tenant-status stream (ADR-0033 dec 3): a consumer outside
    # any group, from the newest offset, so every replica hears every tenant within one interval.
    global _status_task
    _status_task = asyncio.create_task(tenant_status.follow(lambda: AIOKafkaConsumer(
        settings.KAFKA_TOPIC_TENANT_STATUS,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP_SERVERS,
        group_id=None,
        enable_auto_commit=False,
        auto_offset_reset="latest",
        # The publisher's first send creates the topic; notice it within a status interval or so,
        # not the client's five-minute default.
        metadata_max_age_ms=30_000,
    )))
    logger.info(f"Tenant status: {settings.KAFKA_TOPIC_TENANT_STATUS}")

    logger.info("✅ Ingestion Service started successfully")
    logger.info("=" * 80)

@app.on_event("shutdown")
async def shutdown_event():
    """Clean shutdown"""
    logger.info("🛑 Ingestion Service shutting down...")

    if _status_task is not None:
        _status_task.cancel()

    # Close Kafka producer
    await AsyncKafkaProducerSingleton.close()

    logger.info("✅ Ingestion Service stopped")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "ingestion",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "port": settings.INGESTION_SERVICE_PORT
    }

@app.post(
    "/ingest",
    response_model=SensorDataResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest sensor data (device mTLS)",
    description="""
    Sensor data ingestion for device/gateway producers.

    **Security (ADR-0002):**
    - Devices authenticate with mTLS at the edge; the tenant is read from the verified
      client-certificate SAN, never from the request body.
    - company_id is forced from the verified identity, preventing cross-tenant injection.
    """
)
async def ingest_sensor_data(
    data: SensorDataInput,
    identity: dict = Depends(get_ingestion_identity)
) -> SensorDataResponse:
    """
    Ingest sensor data. The company_id comes from the verified device certificate — it is
    never taken from the request body (ADR-0002 dec 6).
    """
    try:
        company_id_str = identity["company_id"]
        
        # Build Kafka message
        kafka_message = {
            "timestamp": data.timestamp.isoformat(),
            "sensor_id": str(data.sensor_id),
            "equipment_id": str(data.equipment_id),
            "site_id": data.site_id,
            "company_id": company_id_str,
            "value": data.value,
            "unit": data.unit,
            "quality_code": data.quality_code if data.quality_code is not None else 0
        }
        
        # Send to Kafka
        success = await send_sensor_data(kafka_message)
        
        if not success:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Failed to queue message to Kafka"
            )
        
        return SensorDataResponse(
            status="accepted",
            message="Sensor data queued for processing",
            company_id=company_id_str,
            sensor_id=data.sensor_id,
            timestamp=data.timestamp
        )
        
    except HTTPException:
        raise
    except Exception:
        # The detail stays in the log; the device gets a generic message.
        logger.exception("Ingestion error")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error"
        )

@app.get("/stats")
async def get_ingestion_stats(
    identity: dict = Depends(get_ingestion_identity)
):
    """Get ingestion statistics for the authenticated tenant."""
    return {
        "company_id": identity["company_id"],
        "device_id": identity.get("device_id"),
        "auth": identity.get("source", "mtls"),
        "message": "Ingestion statistics endpoint",
        "status": "active"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=settings.INGESTION_SERVICE_PORT
    )
