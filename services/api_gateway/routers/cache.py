"""Redis cache inspection endpoints with multi-tenant filtering."""
from fastapi import APIRouter, Depends, HTTPException
from typing import Dict, Any
from messaging.redis_client import redis_client
from dependencies import get_current_user_with_company
from models.user import User

router = APIRouter(prefix="/api/cache", tags=["cache"])

@router.get("/sensors")
async def get_cached_sensors(
    current_user: User = Depends(get_current_user_with_company)
) -> Dict[str, Any]:
    """
    Get all sensors for the authenticated user's company.
    Returns the latest value for each sensor from the cache, filtered by company_id.
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
        
        print(f"✅ CACHE: Returning {len(filtered_sensors)} sensors")
        
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
