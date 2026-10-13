# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Background task to update Redis cache with latest sensor data."""
import asyncio
import uuid

from sqlalchemy import text

from database import engine
from dependencies import normalize_company_id_to_schema
from messaging.redis_client import redis_client

async def update_redis_cache():
    """
    Background task that continuously updates Redis with latest sensor values.
    Schema-per-tenant aware: queries from all tenant schemas.
    Runs every 2 seconds.
    """
    print("✓ Redis cache updater started (schema-per-tenant mode)")
    zero_cycles = 0  # consecutive polls with no fresh measurements (throttles the warning below)
    
    while True:
        try:
            # One connection from the gateway's single pool (database.py), held for one sweep.
            async with engine.connect() as conn:
                # Autocommit: one tenant's failed query must not abort the sweep for the others.
                conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
                # Get all tenant schemas
                tenant_schemas = (await conn.execute(text("""
                    SELECT schema_name
                    FROM information_schema.schemata
                    WHERE schema_name LIKE 'tenant\\_%'
                """))).mappings().all()

                total_sensors = 0

                # Query from each tenant schema
                for schema_row in tenant_schemas:
                    # Extract company_id from schema name and rebuild the schema through the one
                    # canonical mapping, so only a UUID-derived identifier reaches the SQL (ADR-0003).
                    # tenant_550e8400_e29b_41d4_a716_446655440000 -> 550e8400-e29b-41d4-a716-446655440000
                    try:
                        company_id = str(uuid.UUID(schema_row['schema_name'].removeprefix('tenant_').replace('_', '-')))
                    except ValueError:
                        continue
                    schema_name = normalize_company_id_to_schema(company_id)

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
                                FROM "{schema_name}".sensor_measurements
                                WHERE time > NOW() - INTERVAL '1 hour'
                                ORDER BY sensor_id, time DESC
                            )
                            SELECT * FROM latest_per_sensor
                            LIMIT 1000
                        """
                        
                        rows = (await conn.execute(text(query))).mappings().all()
                        
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
                    zero_cycles = 0
                    print(f"✓ Updated {total_sensors} sensors in Redis cache from {len(tenant_schemas)} tenants")
                else:
                    # No fresh measurements anywhere in the last hour: the usual cause is the
                    # raw-measurements writer (spark-streaming / kafka_to_timescaledb.py) being
                    # down. Warn on the first zero poll and every ~60s thereafter, not every 2s,
                    # so the UI going dark leaves a signal in the gateway log.
                    if zero_cycles % 30 == 0:
                        print(
                            f"⚠ Cache updater: 0 fresh sensor_measurements in the last hour across "
                            f"{len(tenant_schemas)} tenant(s) — is spark-streaming "
                            f"(raw-measurements writer) running?"
                        )
                    zero_cycles += 1

        except Exception as e:
            print(f"✗ Cache updater error: {e}")
        
        # Wait 2 seconds before next update
        await asyncio.sleep(2)
