"""
Equipment management endpoints
NOTE: In v5.0, sensors have direct equipment_id - no junction table
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from pydantic import BaseModel, Field
from typing import List, Optional
from uuid import UUID
import logging
from dependencies import get_db_with_tenant, get_current_user_with_company
from models.user import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/equipment", tags=["equipment"])

# Pydantic Models
class EquipmentCreate(BaseModel):
    equipment_id: str
    equipment_type: str
    name: str
    description: Optional[str] = None
    site_id: Optional[str] = None
    location: Optional[str] = None
    sensor_count: int = Field(gt=0)
    expected_sensors: List[str] = Field(default_factory=list)
    batch_timeout_seconds: int = Field(default=5, gt=0)
    require_complete_batch: bool = True
    min_sensors_for_partial: Optional[int] = None

class EquipmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sensor_count: Optional[int] = None
    expected_sensors: Optional[List[str]] = None
    batch_timeout_seconds: Optional[int] = None
    require_complete_batch: Optional[bool] = None
    min_sensors_for_partial: Optional[int] = None

class SensorCreate(BaseModel):
    sensor_id: str
    sensor_type: str
    unit: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True

class BulkSensorCreate(BaseModel):
    sensors: List[SensorCreate]

@router.get("")
async def list_equipment(
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """List all equipment for user's company (schema-routed)"""
    result = await db.execute(
        text("SELECT * FROM equipment ORDER BY created_at DESC")
    )
    rows = result.fetchall()
    return [dict(row._mapping) for row in rows]

@router.get("/{equipment_id}")
async def get_equipment(
    equipment_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """Get specific equipment (schema-routed)"""
    result = await db.execute(
        text("SELECT * FROM equipment WHERE equipment_id = :equipment_id"),
        {"equipment_id": equipment_id}
    )
    row = result.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Equipment not found")
    return dict(row._mapping)

@router.post("", status_code=201)
async def create_equipment(
    equipment: EquipmentCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """Create new equipment (schema ensures tenant isolation)"""
    try:
        result = await db.execute(text("""
            INSERT INTO equipment (
                equipment_id, company_id, equipment_type, name, description,
                site_id, location, sensor_count, expected_sensors,
                batch_timeout_seconds, require_complete_batch, min_sensors_for_partial
            ) VALUES (
                :equipment_id, :company_id, :equipment_type, :name, :description,
                :site_id, :location, :sensor_count, :expected_sensors,
                :batch_timeout_seconds, :require_complete_batch, :min_sensors_for_partial
            )
            RETURNING *
        """), {
            "equipment_id": equipment.equipment_id,
            "company_id": str(current_user.company_id),
            "equipment_type": equipment.equipment_type,
            "name": equipment.name,
            "description": equipment.description,
            "site_id": equipment.site_id,
            "location": equipment.location,
            "sensor_count": equipment.sensor_count,
            "expected_sensors": equipment.expected_sensors,
            "batch_timeout_seconds": equipment.batch_timeout_seconds,
            "require_complete_batch": equipment.require_complete_batch,
            "min_sensors_for_partial": equipment.min_sensors_for_partial
        })
        row = result.fetchone()
        await db.commit()
        return dict(row._mapping)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/{equipment_id}")
async def update_equipment(
    equipment_id: str,
    equipment: EquipmentUpdate,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """Update equipment (schema-routed)"""
    updates = []
    params = {"equipment_id": equipment_id}
    
    for field in ['name', 'description', 'sensor_count', 'expected_sensors', 
                  'batch_timeout_seconds', 'require_complete_batch', 'min_sensors_for_partial']:
        value = getattr(equipment, field, None)
        if value is not None:
            updates.append(f"{field} = :{field}")
            params[field] = value
    
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    
    query = f"""
        UPDATE equipment 
        SET {', '.join(updates)}, updated_at = NOW() 
        WHERE equipment_id = :equipment_id 
        RETURNING *
    """
    
    result = await db.execute(text(query), params)
    row = result.fetchone()
    
    if not row:
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    await db.commit()
    return dict(row._mapping)

@router.delete("/{equipment_id}", status_code=204)
async def delete_equipment(
    equipment_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """Delete equipment (schema-routed)"""
    result = await db.execute(
        text("DELETE FROM equipment WHERE equipment_id = :equipment_id"),
        {"equipment_id": equipment_id}
    )
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    await db.commit()

@router.get("/{equipment_id}/sensors")
async def get_equipment_sensors(
    equipment_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """Get all sensors for equipment - NO junction table, direct ownership"""
    # Verify equipment exists
    check = await db.execute(
        text("SELECT 1 FROM equipment WHERE equipment_id = :equipment_id"),
        {"equipment_id": equipment_id}
    )
    if not check.fetchone():
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    # Query sensors directly by equipment_id
    result = await db.execute(
        text("SELECT * FROM sensors WHERE equipment_id = :equipment_id ORDER BY sensor_id"),
        {"equipment_id": equipment_id}
    )
    rows = result.fetchall()
    return [dict(row._mapping) for row in rows]

@router.post("/{equipment_id}/sensors", status_code=201)
async def add_sensor(
    equipment_id: str,
    sensor: SensorCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """Add sensor to equipment - direct ownership, no junction table"""
    # Verify equipment exists
    check = await db.execute(
        text("SELECT 1 FROM equipment WHERE equipment_id = :equipment_id"),
        {"equipment_id": equipment_id}
    )
    if not check.fetchone():
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    try:
        result = await db.execute(text("""
            INSERT INTO sensors (
                sensor_id, equipment_id, sensor_type, unit, description, is_active
            ) VALUES (
                :sensor_id, :equipment_id, :sensor_type, :unit, :description, :is_active
            )
            RETURNING *
        """), {
            "sensor_id": sensor.sensor_id,
            "equipment_id": equipment_id,
            "sensor_type": sensor.sensor_type,
            "unit": sensor.unit,
            "description": sensor.description,
            "is_active": sensor.is_active
        })
        row = result.fetchone()
        await db.commit()
        return dict(row._mapping)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/{equipment_id}/sensors/{sensor_id}", status_code=204)
async def remove_sensor(
    equipment_id: str,
    sensor_id: str,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """Remove sensor from equipment - direct ownership"""
    # Verify equipment exists
    check = await db.execute(
        text("SELECT 1 FROM equipment WHERE equipment_id = :equipment_id"),
        {"equipment_id": equipment_id}
    )
    if not check.fetchone():
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    result = await db.execute(
        text("DELETE FROM sensors WHERE equipment_id = :equipment_id AND sensor_id = :sensor_id"),
        {"equipment_id": equipment_id, "sensor_id": sensor_id}
    )
    
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Sensor not found")
    
    await db.commit()

@router.post("/{equipment_id}/sensors/bulk", status_code=201)
async def add_sensors_bulk(
    equipment_id: str,
    bulk_data: BulkSensorCreate,
    db: AsyncSession = Depends(get_db_with_tenant),
    current_user: User = Depends(get_current_user_with_company)
):
    """Add multiple sensors to equipment in a single transaction"""
    # Verify equipment and get sensor_count
    result = await db.execute(
        text("SELECT sensor_count FROM equipment WHERE equipment_id = :equipment_id"),
        {"equipment_id": equipment_id}
    )
    equipment_row = result.fetchone()
    if not equipment_row:
        raise HTTPException(status_code=404, detail="Equipment not found")
    
    sensor_count = equipment_row.sensor_count
    
    # Validate sensor count
    if len(bulk_data.sensors) != sensor_count:
        raise HTTPException(
            status_code=400,
            detail=f"Sensor count mismatch: equipment expects {sensor_count} sensors, got {len(bulk_data.sensors)}"
        )
    
    # Check for duplicate sensor_ids
    sensor_ids = [s.sensor_id for s in bulk_data.sensors]
    if len(sensor_ids) != len(set(sensor_ids)):
        raise HTTPException(
            status_code=400,
            detail="Duplicate sensor IDs are not allowed"
        )
    
    # Insert all sensors
    inserted_sensors = []
    try:
        for sensor in bulk_data.sensors:
            result = await db.execute(text("""
                INSERT INTO sensors (
                    sensor_id, equipment_id, sensor_type, unit, description, is_active
                ) VALUES (
                    :sensor_id, :equipment_id, :sensor_type, :unit, :description, :is_active
                )
                RETURNING *
            """), {
                "sensor_id": sensor.sensor_id,
                "equipment_id": equipment_id,
                "sensor_type": sensor.sensor_type,
                "unit": sensor.unit,
                "description": sensor.description,
                "is_active": sensor.is_active
            })
            row = result.fetchone()
            inserted_sensors.append(dict(row._mapping))
        
        # Update expected_sensors array
        sensor_ids_ordered = [s.sensor_id for s in bulk_data.sensors]
        await db.execute(text("""
            UPDATE equipment 
            SET expected_sensors = :expected_sensors, updated_at = NOW() 
            WHERE equipment_id = :equipment_id
        """), {
            "expected_sensors": sensor_ids_ordered,
            "equipment_id": equipment_id
        })
        
        await db.commit()
        
        return {
            "message": f"Successfully added {len(inserted_sensors)} sensors",
            "sensors": inserted_sensors
        }
    except Exception as e:
        await db.rollback()
        logger.error(f"Error in bulk sensor insert: {e}")
        raise HTTPException(status_code=400, detail=f"Failed to insert sensors: {str(e)}")
