# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WebSocket endpoints for real-time data streaming with tenant isolation."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from typing import Optional
import asyncio
import time
from jose import jwt, JWTError
from messaging.redis_client import redis_client
from config import get_settings
from database import AsyncSessionLocal
from sqlalchemy import select, text
from models.user import User

router = APIRouter(prefix="/ws", tags=["websocket"])

# Get settings for JWT
settings = get_settings()
SECRET = settings.JWT_SECRET_KEY

# Track active WebSocket connections with their company_id
active_connections: dict[WebSocket, str] = {}

# Cache for sensor/equipment names to reduce DB queries
_names_cache = {}
_cache_timestamp = 0
_cache_ttl = 60  # Refresh cache every 60 seconds


async def enrich_sensors_with_names(sensors: dict, company_id: str) -> dict:
    """
    Enrich sensor data with sensor_name and equipment_name from database.
    Uses caching to minimize database queries.
    """
    global _names_cache, _cache_timestamp

    current_time = time.time()

    # Refresh cache if expired or empty
    if current_time - _cache_timestamp > _cache_ttl or not _names_cache:
        try:
            async with AsyncSessionLocal() as session:
                # Set search path for tenant schema
                schema_name = f"tenant_{company_id.replace('-', '_')}"
                await session.execute(text(f"SET search_path TO {schema_name}, public"))

                sensor_ids = list(sensors.keys())
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
                    result = await session.execute(
                        text(query),
                        {"sensor_ids": sensor_ids}
                    )
                    rows = result.fetchall()

                    _names_cache = {
                        row[0]: {
                            "sensor_name": row[1],
                            "equipment_name": row[3]
                        }
                        for row in rows
                    }
                    _cache_timestamp = current_time
        except Exception as e:
            print(f"⚠️ Error loading names cache: {e}")

    # Enrich sensor data with names from cache
    for sensor_id, data in sensors.items():
        if sensor_id in _names_cache:
            data['sensor_name'] = _names_cache[sensor_id]['sensor_name']
            data['equipment_name'] = _names_cache[sensor_id]['equipment_name']

    return sensors


async def get_user_from_token(token: str) -> Optional[User]:
    """
    Decode JWT token and fetch user from database.
    Returns None if token is invalid or user not found.
    """
    try:
        print(f"🔍 Decoding JWT token...")
        # Decode JWT token
        payload = jwt.decode(token, SECRET, algorithms=["HS256"], audience="fastapi-users:auth")
        user_id: str = payload.get("sub")
        print(f"🔍 Token decoded successfully, user_id: {user_id}")
        
        if user_id is None:
            print("❌ No user_id in token")
            return None
            
        # Fetch user from database
        print(f"🔍 Fetching user from database...")
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if user:
                print(f"✅ User found: {user.email}, company_id: {user.company_id}")
            else:
                print(f"❌ User not found in database")
            return user
            
    except JWTError as e:
        print(f"❌ JWTError: {e}")
        return None
    except Exception as e:
        print(f"❌ Error validating token: {e}")
        return None


@router.websocket("/sensors")
async def websocket_sensors(websocket: WebSocket, token: Optional[str] = None):
    """
    WebSocket endpoint for real-time sensor data with tenant isolation.
    """
    print(f"🟢 WebSocket /sensors connection attempt - token present: {token is not None}")
    
    # Validate authentication
    if not token:
        print("❌ WebSocket rejected: Missing token")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        return
    
    user = await get_user_from_token(token)
    if not user or not user.company_id:
        print(f"❌ WebSocket rejected: Invalid token or no company")
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token or no company")
        return
    
    await websocket.accept()
    company_id = str(user.company_id)
    active_connections[websocket] = company_id
    
    print(f"✅ WebSocket CONNECTED - Company: {company_id}, User: {user.email}")
    
    try:
        while True:
            try:
                all_sensors = await redis_client.get_all_sensors()

                # Filter sensors by company_id
                company_sensors = {
                    sensor_id: data
                    for sensor_id, data in all_sensors.items()
                    if str(data.get('company_id')) == company_id
                }

                # Enrich with sensor and equipment names
                company_sensors = await enrich_sensors_with_names(company_sensors, company_id)

                print(f"📊 Sending {len(company_sensors)}/{len(all_sensors)} sensors to {user.email}")

                await websocket.send_json({
                    "type": "sensor_update",
                    "timestamp": time.time(),
                    "sensors": company_sensors,
                    "count": len(company_sensors)
                })

                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"❌ Error in sensor loop: {e}")
                await asyncio.sleep(1)
                
    except WebSocketDisconnect:
        active_connections.pop(websocket, None)
        print(f"❌ WebSocket DISCONNECTED - {user.email}")
    except Exception as e:
        print(f"❌ WebSocket error: {e}")
        active_connections.pop(websocket, None)


@router.websocket("/sensors/{equipment_id}")
async def websocket_equipment_sensors(
    websocket: WebSocket,
    equipment_id: str,
    token: Optional[str] = None
):
    """WebSocket for specific equipment."""
    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Missing token")
        return
    
    user = await get_user_from_token(token)
    if not user or not user.company_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid token")
        return
    
    await websocket.accept()
    company_id = str(user.company_id)
    active_connections[websocket] = company_id
    
    try:
        while True:
            all_sensors = await redis_client.get_all_sensors()
            equipment_sensors = {
                sensor_id: data
                for sensor_id, data in all_sensors.items()
                if (data.get('equipment_id') == equipment_id and
                    str(data.get('company_id')) == company_id)
            }

            # Enrich with sensor and equipment names
            equipment_sensors = await enrich_sensors_with_names(equipment_sensors, company_id)

            await websocket.send_json({
                "type": "sensor_update",
                "timestamp": time.time(),
                "equipment_id": equipment_id,
                "sensors": equipment_sensors,
                "count": len(equipment_sensors)
            })

            await asyncio.sleep(1)
                
    except WebSocketDisconnect:
        active_connections.pop(websocket, None)
    except Exception as e:
        active_connections.pop(websocket, None)
