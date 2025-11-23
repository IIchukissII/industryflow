"""Redis cache inspection endpoints with multi-tenant filtering."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from typing import Dict, Any
from messaging.redis_client import redis_client
from dependencies import get_current_user_with_company, get_db_with_tenant
from models.user import User

router = APIRouter(prefix="/api/cache", tags=["cache"])

@router.get("/sensors")
async def get_cached_sensors(
    current_user: User = Depends(get_current_user_with_company),
    db: AsyncSession = Depends(get_db_with_tenant)
) -> Dict[str, Any]:
    """
    Get all sensors for the authenticated user's company.
    Returns the latest value for each sensor from the cache, filtered by company_id.
    Enriched with sensor names and equipment names from database.
    """
    try:
        # Check if Redis is connected
        if redis_client.redis is None:
            raise HTTPException(status_code=503, detail="Redis not connected")

        # Get all sensors from cache
        all_sensors = await redis_client.get_all_sensors()

        # Filter by user's company_id
        company_id = str(current_user.company_id)

        print(f"🔍 CACHE: Filtering {len(all_sensors)} sensors for company {company_id}")

        filtered_sensors = {
            sensor_id: data
            for sensor_id, data in all_sensors.items()
            if data.get('company_id') == company_id
        }

        # Query database to get sensor names and equipment names
        sensor_ids = list(filtered_sensors.keys())
        if sensor_ids:
            query = """
                SELECT
                    s.sensor_id::text,
                    s.sensor_name,
                    s.equipment_id::text,
                    e.name as equipment_name
                FROM sensors s
                LEFT JOIN equipment e ON s.equipment_id = e.equipment_id
                WHERE s.sensor_id = ANY(:sensor_ids)
            """
            result = await db.execute(
                text(query),
                {"sensor_ids": sensor_ids}
            )
            rows = result.fetchall()

            # Create lookup dict for names
            names_lookup = {
                row[0]: {
                    "sensor_name": row[1],
                    "equipment_name": row[3]
                }
                for row in rows
            }

            # Enrich sensor data with names
            for sensor_id, data in filtered_sensors.items():
                if sensor_id in names_lookup:
                    data['sensor_name'] = names_lookup[sensor_id]['sensor_name']
                    data['equipment_name'] = names_lookup[sensor_id]['equipment_name']

        print(f"✅ CACHE: Returning {len(filtered_sensors)} sensors with names")

        return {
            "cached_sensors": len(filtered_sensors),
            "sensors": filtered_sensors,
            "company_id": company_id
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ Cache endpoint error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
