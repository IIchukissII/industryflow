# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Repository layer for Alert Service Worker
Schema-per-tenant architecture
"""
import asyncpg
from datetime import datetime
from typing import List, Dict, Optional
import uuid as uuid_module
import logging

logger = logging.getLogger(__name__)


def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Convert a company_id to its tenant schema name.

    Validates company_id as a UUID first so the result is safe to interpolate into a
    SET search_path statement; a non-UUID raises ValueError instead of reaching SQL
    (defends against injection — see ADR-0003). This path is reachable from untrusted
    Kafka payloads, so the validation is load-bearing.
    """
    canonical = str(uuid_module.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


class AlertRepository:
    """Repository for alert operations with schema-per-tenant"""
    
    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool
    
    async def get_active_rules(self, company_id: str) -> List[Dict]:
        """Get active alert rules for a tenant"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            # Set search path to tenant schema
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            # Query without company_id filter (schema routing handles isolation)
            rows = await conn.fetch("""
                SELECT 
                    rule_id,
                    name,
                    description,
                    sensor_id,
                    equipment_id,
                    sensor_pattern,
                    site_id,
                    detection_type,
                    condition,
                    threshold,
                    threshold_min,
                    threshold_max,
                    model_id,
                    anomaly_threshold,
                    model_config,
                    requires_complete_batch,
                    min_batch_completeness,
                    severity,
                    priority,
                    enabled,
                    created_at,
                    updated_at,
                    created_by
                FROM alert_rules
                WHERE enabled = true
                ORDER BY priority DESC, created_at ASC
            """)
            
            return [dict(row) for row in rows]
    
    async def save_alert(self, alert_data: Dict) -> str:
        """Save alert to database with schema routing"""
        company_id = alert_data['company_id']
        schema_name = normalize_company_id_to_schema(company_id)

        # Generate alert_id if not provided
        alert_id = alert_data.get('alert_id', str(uuid_module.uuid4()))
        triggered_at = alert_data.get('triggered_at', datetime.utcnow())

        async with self.pool.acquire() as conn:
            # Set search path to tenant schema
            await conn.execute(f"SET search_path TO {schema_name}, public")

            # Insert without company_id column
            await conn.execute("""
                INSERT INTO alerts (
                    alert_id,
                    triggered_at,
                    rule_id,
                    sensor_id,
                    equipment_id,
                    site_id,
                    detection_type,
                    threshold_value,
                    actual_value,
                    condition,
                    model_id,
                    anomaly_score,
                    severity,
                    message,
                    acknowledged
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
            """,
                alert_id,
                triggered_at,
                alert_data['rule_id'],
                alert_data.get('sensor_id'),
                alert_data.get('equipment_id'),
                alert_data.get('site_id'),
                alert_data['detection_type'],
                alert_data.get('threshold_value'),
                alert_data.get('actual_value'),
                alert_data.get('condition'),
                alert_data.get('model_id'),
                alert_data.get('anomaly_score'),
                alert_data['severity'],
                alert_data['message'],
                False  # acknowledged
            )
        
        logger.info(f"Saved alert {alert_id} to {schema_name}")
        return alert_id
    
    async def get_tenant_schemas(self) -> List[str]:
        """Get all active tenant schemas"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name LIKE 'tenant_%'
                ORDER BY schema_name
            """)
            return [row['schema_name'] for row in rows]
    
    async def get_company_id_from_schema(self, schema_name: str) -> str:
        """Convert schema name back to company_id (UUID format)"""
        # tenant_550e8400_e29b_41d4_a716_446655440000 -> 550e8400-e29b-41d4-a716-446655440000
        uuid_part = schema_name.replace('tenant_', '').replace('_', '-')
        return uuid_part

    async def get_sensor_name(self, sensor_id: str, company_id: str) -> Optional[str]:
        """Get sensor name from sensor UUID"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            # Set search path to tenant schema
            await conn.execute(f"SET search_path TO {schema_name}, public")

            # Query sensor name
            sensor_name = await conn.fetchval(
                "SELECT sensor_name FROM sensors WHERE sensor_id = $1",
                uuid_module.UUID(sensor_id)
            )

            return sensor_name

    async def get_feature_config(self, config_id: str, company_id: str) -> Optional[Dict]:
        """Get feature engineering config by ID"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            # Set search path to tenant schema
            await conn.execute(f"SET search_path TO {schema_name}, public")

            # Query feature config
            row = await conn.fetchrow(
                """
                SELECT id, name, base_sensors, transformations
                FROM feature_engineering_configs
                WHERE id = $1
                """,
                uuid_module.UUID(config_id)
            )

            return dict(row) if row else None

    async def get_model_by_id(self, model_id: str, company_id: str) -> Optional[Dict]:
        """Get ML model metadata by ID"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            # Set search_path to tenant schema
            await conn.execute(f"SET search_path TO {schema_name}, public")

            # Query model
            row = await conn.fetchrow(
                """
                SELECT model_id, model_name, model_version, status,
                       mlflow_run_id, feature_config_id
                FROM ml_models
                WHERE model_id = $1
                """,
                uuid_module.UUID(model_id)
            )

            return dict(row) if row else None


class RuleRepository:
    """Repository for loading rules from all tenants"""
    
    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool
    
    async def load_all_tenant_rules(self) -> Dict[str, List[Dict]]:
        """
        Load rules from all tenant schemas
        Returns: {company_id: [rules]}
        """
        alert_repo = AlertRepository(self.pool)
        schemas = await alert_repo.get_tenant_schemas()
        
        all_rules = {}
        
        for schema in schemas:
            company_id = await alert_repo.get_company_id_from_schema(schema)
            
            try:
                rules = await alert_repo.get_active_rules(company_id)
                all_rules[company_id] = rules
                logger.info(f"Loaded {len(rules)} rules for company {company_id}")
            except Exception as e:
                logger.error(f"Failed to load rules for {company_id}: {e}")
                all_rules[company_id] = []
        
        return all_rules
