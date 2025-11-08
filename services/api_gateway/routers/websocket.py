"""WebSocket endpoints for real-time data streaming with tenant isolation."""
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from typing import Optional
import asyncio
import time
from jose import jwt, JWTError
from messaging.redis_client import redis_client
from config import get_settings
from database import AsyncSessionLocal
from sqlalchemy import select
from models.user import User

router = APIRouter(prefix="/ws", tags=["websocket"])

# Get settings for JWT
settings = get_settings()
SECRET = settings.jwt_secret_key if hasattr(settings, 'jwt_secret_key') else "CHANGE-THIS-TO-A-RANDOM-SECRET-KEY"

# Track active WebSocket connections with their company_id
active_connections: dict[WebSocket, str] = {}


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
