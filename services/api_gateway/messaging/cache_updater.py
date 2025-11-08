"""Background task to update Redis cache with latest sensor data."""
import asyncio
from datetime import datetime
from sqlalchemy import text
from database import get_db_pool
from messaging.redis_client import redis_client

async def update_redis_cache():
    """
    Background task that continuously updates Redis with latest sensor values.
    Schema-per-tenant aware: queries from all tenant schemas.
    Runs every 2 seconds.
    """
    print("✓ Redis cache updater started (schema-per-tenant mode)")
    
    while True:
        try:
            pool = await get_db_pool()
            async with pool.acquire() as conn:
                # Get all tenant schemas
                tenant_schemas = await conn.fetch("""
                    SELECT schema_name 
                    FROM information_schema.schemata 
                    WHERE schema_name LIKE 'tenant_%'
                """)
                
                total_sensors = 0
                
                # Query from each tenant schema
                for schema_row in tenant_schemas:
                    schema_name = schema_row['schema_name']
                    
                    # Extract company_id from schema name
                    # tenant_550e8400_e29b_41d4_a716_446655440000 -> 550e8400-e29b-41d4-a716-446655440000
                    company_id = schema_name.replace('tenant_', '').replace('_', '-')
                    
                    try:
                        # Query latest value for each sensor in this tenant
                        # Note: No company_id in SELECT - it's implicit in schema
                        query = f"""
                            WITH latest_per_sensor AS (
                                SELECT DISTINCT ON (sensor_id)
                                    time,
                                    sensor_id,
                                    equipment_id,
                                    site_id,
                                    value,
                                    unit,
                                    quality_code
                                FROM {schema_name}.sensor_measurements
                                WHERE time > NOW() - INTERVAL '1 hour'
                                ORDER BY sensor_id, time DESC
                            )
                            SELECT * FROM latest_per_sensor
                            LIMIT 1000
                        """
                        
                        rows = await conn.fetch(query)
                        
                        # Update Redis for each sensor
                        for row in rows:
                            await redis_client.set_sensor_value(
                                sensor_id=str(row['sensor_id']),
                                value=float(row['value']),
                                metadata={
                                    "timestamp": row['time'].isoformat() if row['time'] else None,
                                    "equipment_id": str(row['equipment_id']) if row['equipment_id'] else None,
                                    "site_id": row['site_id'],
                                    "company_id": company_id,  # Derived from schema name
                                    "unit": row['unit'],
                                    "quality_code": row['quality_code']
                                },
                                ttl=60
                            )
                        
                        total_sensors += len(rows)
                        
                    except Exception as e:
                        print(f"✗ Error querying {schema_name}: {e}")
                        continue
                
                if total_sensors > 0:
                    print(f"✓ Updated {total_sensors} sensors in Redis cache from {len(tenant_schemas)} tenants")
                
        except Exception as e:
            print(f"✗ Cache updater error: {e}")
        
        # Wait 2 seconds before next update
        await asyncio.sleep(2)
