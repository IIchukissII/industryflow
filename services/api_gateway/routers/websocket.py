# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""WebSocket endpoints for real-time data streaming with tenant isolation.

The socket authenticates from the httpOnly access cookie on the wss:// handshake, never from a
URL parameter: a token in the URL lands in proxy and server access logs (ADR-0004 dec 3, 7). A
socket lives no longer than the session that opened it: it closes when the access token expires
and when its user is deactivated or deleted, so the client reconnects with a fresh cookie.
"""
from collections import OrderedDict
from dataclasses import dataclass
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from typing import Optional
import asyncio
import time
from jose import jwt, JWTError
from messaging.redis_client import redis_client
from config import get_settings
from database import AsyncSessionLocal
from dependencies import normalize_company_id_to_schema
from users import ACCESS_COOKIE
from sqlalchemy import select, text
from models.user import User

router = APIRouter(prefix="/ws", tags=["websocket"])

# Get settings for JWT
settings = get_settings()
SECRET = settings.JWT_SECRET_KEY

# How often an open socket re-checks that its user is still active.
USER_RECHECK_SECONDS = 60

# Close code (application range) for "the access token expired or is missing": the client
# refreshes its session (POST /auth/refresh) and reconnects. 1008 means the user may not
# connect at all (deactivated, deleted, no company) and is final.
CLOSE_SESSION_EXPIRED = 4401

# Track active WebSocket connections with their company_id
active_connections: dict[WebSocket, str] = {}

# Sensor/equipment names, cached PER TENANT: {company_id: (loaded_at, {sensor_id: names})}.
# Bounded (least recently used tenant evicted first) so it cannot grow with the tenant count.
_cache_ttl = 60  # Refresh a tenant's names every 60 seconds
_cache_max_tenants = 256
_names_cache: "OrderedDict[str, tuple[float, dict]]" = OrderedDict()


async def _load_names(company_id: str, sensor_ids: list) -> dict:
    """Names of the given sensors and their equipment, read from the tenant's own schema."""
    async with AsyncSessionLocal() as session:
        # SET LOCAL so the tenant path is scoped to this transaction and discarded
        # when it ends; it never lingers on the pooled connection (ADR-0003 dec 6).
        async with session.begin():
            schema_name = normalize_company_id_to_schema(company_id)
            await session.execute(text(f"SET LOCAL search_path TO {schema_name}, public"))
            result = await session.execute(
                text("""
                    SELECT
                        s.sensor_id::text,
                        s.sensor_name,
                        s.equipment_id::text,
                        e.name as equipment_name
                    FROM sensors s
                    LEFT JOIN equipment e ON s.equipment_id = e.equipment_id
                    WHERE s.sensor_id = ANY(:sensor_ids)
                """),
                {"sensor_ids": sensor_ids},
            )
            return {
                row[0]: {"sensor_name": row[1], "equipment_name": row[3]}
                for row in result.fetchall()
            }


async def enrich_sensors_with_names(sensors: dict, company_id: str) -> dict:
    """
    Enrich sensor data with sensor_name and equipment_name from database.
    Uses a per-tenant cache to minimize database queries: one tenant's names are never
    served to, or overwritten by, another tenant.
    """
    current_time = time.time()
    cached = _names_cache.get(company_id)

    # Refresh this tenant's names if expired or never loaded
    if cached is None or current_time - cached[0] > _cache_ttl:
        sensor_ids = list(sensors.keys())
        if sensor_ids:
            try:
                cached = (current_time, await _load_names(company_id, sensor_ids))
                _names_cache[company_id] = cached
            except Exception as e:
                print(f"⚠️ Error loading names cache: {e}")
    if cached is not None:
        _names_cache.move_to_end(company_id)
        while len(_names_cache) > _cache_max_tenants:
            _names_cache.popitem(last=False)

    # Enrich sensor data with names from cache
    names = cached[1] if cached else {}
    for sensor_id, data in sensors.items():
        if sensor_id in names:
            data['sensor_name'] = names[sensor_id]['sensor_name']
            data['equipment_name'] = names[sensor_id]['equipment_name']

    return sensors


@dataclass
class SocketSession:
    """The authenticated user of a socket and when its access token expires (epoch seconds)."""
    user: User
    expires_at: float


async def _load_active_user(user_id: str) -> Optional[User]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        return None
    return user


def _token_claims(token: str) -> Optional[dict]:
    """The verified claims of an unexpired access JWT that names a user and an expiry, else None."""
    try:
        payload = jwt.decode(token, SECRET, algorithms=["HS256"], audience="fastapi-users:auth")
    except JWTError:
        return None
    if payload.get("sub") is None or payload.get("exp") is None:
        return None
    return payload


async def get_user_from_token(token: str) -> Optional[SocketSession]:
    """
    Verify the access JWT and load its user. Returns None if the token is invalid or expired,
    or the user does not exist or is inactive.
    """
    payload = _token_claims(token)
    if payload is None:
        return None
    user_id = payload["sub"]
    expires_at = payload["exp"]
    try:
        user = await _load_active_user(user_id)
    except Exception as e:
        print(f"❌ Error validating websocket session: {e}")
        return None
    if user is None:
        return None
    return SocketSession(user=user, expires_at=float(expires_at))


async def _authenticate(websocket: WebSocket) -> Optional[SocketSession]:
    """
    Authenticate the handshake from the access cookie only. On failure the socket is accepted
    and immediately closed with a code the client can act on (a close before accept reaches a
    browser only as an opaque 1006): CLOSE_SESSION_EXPIRED when refreshing the session may
    help, 1008 when it cannot. Nothing is sent before the close.
    """
    token = websocket.cookies.get(ACCESS_COOKIE)
    if not token:
        await websocket.accept()
        await websocket.close(code=CLOSE_SESSION_EXPIRED, reason="No session")
        return None
    claims = _token_claims(token)
    if claims is None:
        await websocket.accept()
        await websocket.close(code=CLOSE_SESSION_EXPIRED, reason="Session expired")
        return None
    session = await get_user_from_token(token)
    if session is None or not session.user.company_id:
        await websocket.accept()
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason="Invalid session or no company")
        return None
    return session


class _SessionWatch:
    """Decides, each tick, whether the socket's session is still good."""

    def __init__(self, session: SocketSession):
        self.session = session
        self.checked_at = time.time()

    async def close_code(self) -> Optional[int]:
        """None while the session holds; else the close code to end the socket with."""
        now = time.time()
        if now >= self.session.expires_at:
            return CLOSE_SESSION_EXPIRED
        if now - self.checked_at >= USER_RECHECK_SECONDS:
            self.checked_at = now
            try:
                if await _load_active_user(str(self.session.user.id)) is None:
                    return status.WS_1008_POLICY_VIOLATION
            except Exception as e:
                # A transient DB error is not a revocation; check again next interval.
                print(f"⚠️ Could not re-check websocket user: {e}")
        return None


async def _stream(websocket: WebSocket, session: SocketSession, select_sensors, extra: dict) -> None:
    company_id = str(session.user.company_id)
    active_connections[websocket] = company_id
    watch = _SessionWatch(session)
    try:
        while True:
            code = await watch.close_code()
            if code is not None:
                await websocket.close(code=code, reason="Session ended")
                return

            # Build the update. A transient data-side error (Redis/DB hiccup) is logged and
            # retried WITHOUT dropping the socket — keep `continue` scoped to data only.
            try:
                all_sensors = await redis_client.get_all_sensors()
                sensors = {
                    sensor_id: data
                    for sensor_id, data in all_sensors.items()
                    if str(data.get('company_id')) == company_id and select_sensors(data)
                }

                # Enrich with sensor and equipment names
                sensors = await enrich_sensors_with_names(sensors, company_id)
            except Exception as e:
                print(f"⚠️ Error building sensor update: {e}")
                await asyncio.sleep(1)
                continue

            # The send is OUTSIDE the data try: if it fails the client has gone away (e.g. a 1001
            # "going away" close), so let it propagate out of the loop to be cleaned up below —
            # NEVER catch-and-retry, which would spin forever on a dead socket (zombie loop).
            await websocket.send_json({
                "type": "sensor_update",
                "timestamp": time.time(),
                **extra,
                "sensors": sensors,
                "count": len(sensors)
            })
            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        # A closed/broken connection (ConnectionClosed, 1001, etc.) ends the coroutine cleanly.
        print(f"❌ WebSocket closed: {e}")
    finally:
        active_connections.pop(websocket, None)


@router.websocket("/sensors")
async def websocket_sensors(websocket: WebSocket):
    """
    WebSocket endpoint for real-time sensor data with tenant isolation.
    """
    session = await _authenticate(websocket)
    if session is None:
        return
    await websocket.accept()
    await _stream(websocket, session, lambda data: True, {})


@router.websocket("/sensors/{equipment_id}")
async def websocket_equipment_sensors(websocket: WebSocket, equipment_id: str):
    """WebSocket for specific equipment."""
    session = await _authenticate(websocket)
    if session is None:
        return
    await websocket.accept()
    await _stream(
        websocket, session,
        lambda data: data.get('equipment_id') == equipment_id,
        {"equipment_id": equipment_id},
    )
