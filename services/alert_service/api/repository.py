# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Repository layer for database operations
Schema-per-tenant architecture with search_path routing
"""
import asyncpg
import json
from typing import List, Optional, Dict, Any
from uuid import UUID
import uuid
from datetime import datetime
from config import normalize_company_id_to_schema

try:
    from .models import (
        AlertRule, AlertRuleCreate, AlertRuleUpdate,
        MLModel, MLModelCreate,
        Alert, AlertCreate,
        DetectionType, DetectionModeSwitch
    )
except ImportError:
    from models import (
        AlertRule, AlertRuleCreate, AlertRuleUpdate,
        MLModel, MLModelCreate,
        Alert, AlertCreate,
        DetectionType, DetectionModeSwitch
    )


class AlertRuleRepository:
    """Repository for alert_rules table operations"""

    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool

    async def get_all_active_rules(
        self,
        company_id: str,
        detection_type: Optional[DetectionType] = None,
        enabled_only: bool = True
    ) -> List[AlertRule]:
        """Get all active alert rules for a tenant"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        query = "SELECT * FROM alert_rules WHERE 1=1"
        params = []
        param_count = 1

        if detection_type:
            query += f" AND detection_type = ${param_count}"
            params.append(detection_type.value)
            param_count += 1

        if enabled_only:
            query += " AND enabled = true"

        query += " ORDER BY priority DESC, created_at ASC"

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            rows = await conn.fetch(query, *params)
            
            # Inject company_id into responses
            return [
                AlertRule(**{**dict(row), 'company_id': company_id})
                for row in rows
            ]

    async def get_rule_by_id(
        self,
        rule_id: UUID,
        company_id: str
    ) -> Optional[AlertRule]:
        """Get single alert rule by ID"""
        schema_name = normalize_company_id_to_schema(company_id)
        query = "SELECT * FROM alert_rules WHERE rule_id = $1"

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            row = await conn.fetchrow(query, rule_id)
            
            if row:
                return AlertRule(**{**dict(row), 'company_id': company_id})
            return None

    async def create_rule(
        self,
        rule_data: AlertRuleCreate,
        company_id: str
    ) -> AlertRule:
        """Create new alert rule"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        query = """
            INSERT INTO alert_rules (
                name, description,
                sensor_pattern, sensor_id, equipment_id, site_id,
                detection_type,
                condition, threshold, threshold_min, threshold_max,
                model_id, anomaly_threshold, model_config,
                severity, priority, enabled,
                created_by
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                $12, $13, $14, $15, $16, $17, $18
            )
            RETURNING *
        """

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            row = await conn.fetchrow(
                query,
                rule_data.name,
                rule_data.description,
                rule_data.sensor_pattern,
                rule_data.sensor_id,
                rule_data.equipment_id,
                rule_data.site_id,
                rule_data.detection_type.value,
                rule_data.condition.value if rule_data.condition else None,
                rule_data.threshold,
                rule_data.threshold_min,
                rule_data.threshold_max,
                rule_data.model_id,
                rule_data.anomaly_threshold,
                json.dumps(rule_data.ml_model_config) if rule_data.ml_model_config else None,
                rule_data.severity.value,
                rule_data.priority,
                rule_data.enabled,
                rule_data.created_by
            )

            return AlertRule(**{**dict(row), 'company_id': company_id})

    async def update_rule(
        self,
        rule_id: UUID,
        rule_data: AlertRuleUpdate,
        company_id: str
    ) -> AlertRule:
        """Update existing alert rule"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        # Build dynamic update query
        update_fields = []
        params = []
        param_count = 1

        if rule_data.name is not None:
            update_fields.append(f"name = ${param_count}")
            params.append(rule_data.name)
            param_count += 1

        if rule_data.description is not None:
            update_fields.append(f"description = ${param_count}")
            params.append(rule_data.description)
            param_count += 1

        if rule_data.sensor_pattern is not None:
            update_fields.append(f"sensor_pattern = ${param_count}")
            params.append(rule_data.sensor_pattern)
            param_count += 1

        if rule_data.sensor_id is not None:
            update_fields.append(f"sensor_id = ${param_count}")
            params.append(rule_data.sensor_id)
            param_count += 1

        if rule_data.equipment_id is not None:
            update_fields.append(f"equipment_id = ${param_count}")
            params.append(rule_data.equipment_id)
            param_count += 1

        if rule_data.site_id is not None:
            update_fields.append(f"site_id = ${param_count}")
            params.append(rule_data.site_id)
            param_count += 1

        if rule_data.detection_type is not None:
            update_fields.append(f"detection_type = ${param_count}")
            params.append(rule_data.detection_type.value)
            param_count += 1

        if rule_data.condition is not None:
            update_fields.append(f"condition = ${param_count}")
            params.append(rule_data.condition.value if rule_data.condition else None)
            param_count += 1

        if rule_data.threshold is not None:
            update_fields.append(f"threshold = ${param_count}")
            params.append(rule_data.threshold)
            param_count += 1

        if rule_data.threshold_min is not None:
            update_fields.append(f"threshold_min = ${param_count}")
            params.append(rule_data.threshold_min)
            param_count += 1

        if rule_data.threshold_max is not None:
            update_fields.append(f"threshold_max = ${param_count}")
            params.append(rule_data.threshold_max)
            param_count += 1

        if rule_data.model_id is not None:
            update_fields.append(f"model_id = ${param_count}")
            params.append(str(rule_data.model_id) if rule_data.model_id else None)
            param_count += 1

        if rule_data.anomaly_threshold is not None:
            update_fields.append(f"anomaly_threshold = ${param_count}")
            params.append(rule_data.anomaly_threshold)
            param_count += 1

        if rule_data.ml_model_config is not None:
            update_fields.append(f"model_config = ${param_count}")
            params.append(json.dumps(rule_data.ml_model_config) if rule_data.ml_model_config else None)
            param_count += 1

        if rule_data.severity is not None:
            update_fields.append(f"severity = ${param_count}")
            params.append(rule_data.severity.value)
            param_count += 1

        if rule_data.priority is not None:
            update_fields.append(f"priority = ${param_count}")
            params.append(rule_data.priority)
            param_count += 1

        if rule_data.enabled is not None:
            update_fields.append(f"enabled = ${param_count}")
            params.append(rule_data.enabled)
            param_count += 1

        if not update_fields:
            return await self.get_rule_by_id(rule_id, company_id)

        update_fields.append(f"updated_at = NOW()")
        params.append(rule_id)

        query = f"""
            UPDATE alert_rules
            SET {', '.join(update_fields)}
            WHERE rule_id = ${param_count}
            RETURNING *
        """

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            row = await conn.fetchrow(query, *params)

            if not row:
                raise ValueError(f"Rule {rule_id} not found")

            return AlertRule(**{**dict(row), 'company_id': company_id})

    async def delete_rule(
        self,
        rule_id: UUID,
        company_id: str
    ) -> bool:
        """Delete alert rule"""
        schema_name = normalize_company_id_to_schema(company_id)
        query = "DELETE FROM alert_rules WHERE rule_id = $1"

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            result = await conn.execute(query, rule_id)
            return result == "DELETE 1"

    async def switch_detection_mode(
        self,
        rule_id: UUID,
        mode_switch: DetectionModeSwitch,
        company_id: str
    ) -> bool:
        """Switch detection mode for a rule"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        update_fields = [f"detection_type = $1"]
        params = [mode_switch.detection_type.value]
        param_count = 2

        if mode_switch.detection_type == DetectionType.THRESHOLD:
            if mode_switch.condition is not None:
                update_fields.append(f"condition = ${param_count}")
                params.append(mode_switch.condition.value)
                param_count += 1

            if mode_switch.threshold is not None:
                update_fields.append(f"threshold = ${param_count}")
                params.append(mode_switch.threshold)
                param_count += 1

            if mode_switch.threshold_min is not None:
                update_fields.append(f"threshold_min = ${param_count}")
                params.append(mode_switch.threshold_min)
                param_count += 1

            if mode_switch.threshold_max is not None:
                update_fields.append(f"threshold_max = ${param_count}")
                params.append(mode_switch.threshold_max)
                param_count += 1

        if mode_switch.detection_type == DetectionType.ML:
            if mode_switch.model_id is not None:
                update_fields.append(f"model_id = ${param_count}")
                params.append(mode_switch.model_id)
                param_count += 1

            if mode_switch.anomaly_threshold is not None:
                update_fields.append(f"anomaly_threshold = ${param_count}")
                params.append(mode_switch.anomaly_threshold)
                param_count += 1

            if mode_switch.ml_model_config is not None:
                update_fields.append(f"model_config = ${param_count}")
                params.append(mode_switch.ml_model_config)
                param_count += 1

        query = f"""
            UPDATE alert_rules
            SET {', '.join(update_fields)}
            WHERE rule_id = ${param_count}
        """
        params.append(rule_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            result = await conn.execute(query, *params)
            return result == "UPDATE 1"


class MLModelRepository:
    """Repository for ml_models table operations"""

    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool

    async def get_all_models(
        self,
        company_id: str,
        status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Get all ML models for a tenant"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        query = "SELECT * FROM ml_models WHERE 1=1"
        params = []
        param_count = 1
        
        if status:
            query += f" AND status = ${param_count}"
            params.append(status)
            param_count += 1
            
        query += " ORDER BY created_at DESC"
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            rows = await conn.fetch(query, *params)
            
            return [
                {**dict(row), 'company_id': company_id}
                for row in rows
            ]

    async def get_model_by_id(
        self,
        model_id: UUID,
        company_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get single ML model by ID"""
        schema_name = normalize_company_id_to_schema(company_id)
        query = "SELECT * FROM ml_models WHERE model_id = $1"

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            row = await conn.fetchrow(query, model_id)
            
            if row:
                return {**dict(row), 'company_id': company_id}
            return None

    async def create_model(
        self,
        model_data: MLModelCreate,
        company_id: str
    ) -> UUID:
        """Create new ML model"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        query = """
            INSERT INTO ml_models (
                equipment_id, model_name, description, model_type,
                model_version, mlflow_run_id, mlflow_experiment_id,
                model_path, training_metrics, hyperparameters,
                feature_names, sensor_ids,
                accuracy, precision_score, recall, f1_score, auc_roc,
                training_samples, training_start_date, training_end_date,
                status, deployed_at, deprecated_at, created_by
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                $13, $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24
            )
            RETURNING model_id
        """

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            row = await conn.fetchrow(
                query,
                model_data.equipment_id,
                model_data.model_name,
                model_data.description,
                model_data.model_type.value,
                model_data.model_version,
                model_data.mlflow_run_id,
                model_data.mlflow_experiment_id,
                model_data.model_path,
                json.dumps(model_data.training_metrics) if model_data.training_metrics else None,
                json.dumps(model_data.hyperparameters) if model_data.hyperparameters else None,
                model_data.feature_names,
                model_data.sensor_ids,
                model_data.accuracy,
                model_data.precision_score,
                model_data.recall,
                model_data.f1_score,
                model_data.auc_roc,
                model_data.training_samples,
                model_data.training_start_date,
                model_data.training_end_date,
                model_data.status.value,
                model_data.deployed_at,
                model_data.deprecated_at,
                model_data.created_by
            )
            return row['model_id']


class AlertRepository:
    """Repository for alerts table operations"""

    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool

    async def save_alert(
        self,
        alert_data: AlertCreate,
        company_id: str
    ) -> UUID:
        """Save new alert to database"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        alert_id = uuid.uuid4()
        triggered_at = alert_data.triggered_at or datetime.utcnow()

        query = """
            INSERT INTO alerts (
                alert_id, rule_id, triggered_at,
                sensor_id, equipment_id, site_id,
                detection_type,
                threshold_value, actual_value, condition,
                model_id, anomaly_score,
                severity, message
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14
            )
            RETURNING alert_id
        """

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            row = await conn.fetchrow(
                query,
                alert_id,
                alert_data.rule_id,
                triggered_at,
                alert_data.sensor_id,
                alert_data.equipment_id,
                alert_data.site_id,
                alert_data.detection_type.value,
                alert_data.threshold_value,
                alert_data.actual_value,
                alert_data.condition,
                alert_data.model_id,
                alert_data.anomaly_score,
                alert_data.severity.value,
                alert_data.message
            )
            return row['alert_id']

    async def get_alerts(
        self,
        company_id: str,
        filters: Dict[str, Any]
    ) -> List[Alert]:
        """Get alerts with filtering"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        query = "SELECT * FROM alerts WHERE 1=1"
        params = []
        param_count = 1

        if 'sensor_id' in filters:
            query += f" AND sensor_id = ${param_count}"
            params.append(filters['sensor_id'])
            param_count += 1

        if 'equipment_id' in filters:
            query += f" AND equipment_id = ${param_count}"
            params.append(filters['equipment_id'])
            param_count += 1

        if 'severity' in filters:
            query += f" AND severity = ${param_count}"
            params.append(filters['severity'])
            param_count += 1

        if 'detection_type' in filters:
            query += f" AND detection_type = ${param_count}"
            params.append(filters['detection_type'])
            param_count += 1

        if 'acknowledged' in filters:
            query += f" AND acknowledged = ${param_count}"
            params.append(filters['acknowledged'])
            param_count += 1

        query += " ORDER BY triggered_at DESC"

        limit = filters.get('limit', 100)
        query += f" LIMIT ${param_count}"
        params.append(limit)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            rows = await conn.fetch(query, *params)
            
            return [
                Alert(**{**dict(row), 'company_id': company_id})
                for row in rows
            ]

    async def acknowledge_alert(
        self,
        alert_id: UUID,
        acknowledged_by: str,
        company_id: str
    ) -> bool:
        """Mark alert as acknowledged"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        query = """
            UPDATE alerts
            SET acknowledged = true,
                acknowledged_at = NOW(),
                acknowledged_by = $1
            WHERE alert_id = $2
        """

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            result = await conn.execute(query, acknowledged_by, alert_id)
            return result == "UPDATE 1"
