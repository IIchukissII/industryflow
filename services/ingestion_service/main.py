"""
IndustryFlow Ingestion Service
High-throughput sensor data ingestion endpoint (Port 8003)
"""
from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
import asyncpg
import logging

from config import get_settings
from schemas import SensorDataInput, SensorDataResponse
from dependencies import get_current_user_with_company, db_pool
from kafka_producer import AsyncKafkaProducerSingleton, send_sensor_data
import dependencies

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

settings = get_settings()

# FastAPI app
app = FastAPI(
    title="IndustryFlow Ingestion Service",
    description="High-throughput sensor data ingestion endpoint",
    version="1.0.0"
)

# CORS
origins = settings.CORS_ORIGINS.split(",") if settings.CORS_ORIGINS != "*" else ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)

@app.on_event("startup")
async def startup_event():
    """Initialize connections on startup"""
    logger.info("=" * 80)
    logger.info("🚀 Ingestion Service starting...")
    logger.info(f"Port: {settings.INGESTION_SERVICE_PORT}")
    logger.info(f"Kafka: {settings.KAFKA_BOOTSTRAP_SERVERS}")
    logger.info(f"Topic: {settings.KAFKA_TOPIC_RAW}")
    logger.info(f"Database: {settings.INGESTION_SERVICE_DB_USER}@{settings.DB_HOST}:{settings.DB_PORT}/{settings.DB_NAME}")
    
    # Initialize database pool (same as API Gateway)
    logger.info("Initializing database connection pool...")
    dependencies.db_pool = await asyncpg.create_pool(
        host=settings.DB_HOST,
        port=settings.DB_PORT,
        database=settings.DB_NAME,
        user=settings.INGESTION_SERVICE_DB_USER,
        password=settings.INGESTION_SERVICE_DB_PASSWORD,
        min_size=5,
        max_size=20,
        command_timeout=60
    )
    logger.info("✅ Database pool created")
    
    # Initialize Kafka producer
    logger.info("Initializing Kafka producer...")
    await AsyncKafkaProducerSingleton.get_producer()
    logger.info("✅ Kafka producer initialized")
    
    logger.info("✅ Ingestion Service started successfully")
    logger.info("=" * 80)

@app.on_event("shutdown")
async def shutdown_event():
    """Clean shutdown"""
    logger.info("🛑 Ingestion Service shutting down...")
    
    # Close Kafka producer
    await AsyncKafkaProducerSingleton.close()
    
    # Close database pool
    if dependencies.db_pool:
        await dependencies.db_pool.close()
        logger.info("✅ Database pool closed")
    
    logger.info("✅ Ingestion Service stopped")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "service": "ingestion",
        "timestamp": datetime.utcnow().isoformat(),
        "port": settings.INGESTION_SERVICE_PORT
    }

@app.post(
    "/ingest",
    response_model=SensorDataResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ingest sensor data (authenticated)",
    description="""
    Secure endpoint for sensor data ingestion.
    
    **Security:**
    - Requires JWT authentication
    - company_id is FORCED from authenticated user's token
    - Prevents cross-tenant data injection
    
    **Usage:**
    1. Login to get JWT token
    2. Include token in Authorization header
    3. Send sensor data (without company_id)
    4. System automatically assigns your company_id
    """
)
async def ingest_sensor_data(
    data: SensorDataInput,
    current_user: dict = Depends(get_current_user_with_company)
) -> SensorDataResponse:
    """
    Ingest authenticated sensor data (OPTIMIZED).
    The company_id is AUTOMATICALLY set from the authenticated user's JWT token.
    """
    try:
        company_id_str = current_user["company_id"]
        
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
    except Exception as e:
        logger.error(f"Ingestion error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Internal server error: {str(e)}"
        )

@app.get("/stats")
async def get_ingestion_stats(
    current_user: dict = Depends(get_current_user_with_company)
):
    """Get ingestion statistics for authenticated company"""
    return {
        "company_id": current_user["company_id"],
        "user_id": current_user["user_id"],
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
