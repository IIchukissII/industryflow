# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Repository Layer - Schema-per-tenant Data Access
Handles queries to both industryflow and mlflow databases
"""
import asyncpg
import logging
import json
import uuid
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def _format_feature_config_row(row: dict, company_id: str) -> Dict[str, Any]:
    """Convert database row to properly formatted feature config dict"""
    return {
        'id': str(row['id']),
        'company_id': company_id,
        'equipment_type': row['equipment_type'],
        'name': row['name'],
        'description': row['description'],
        'base_sensors': json.loads(row['base_sensors']) if isinstance(row['base_sensors'], str) else row['base_sensors'],
        'transformations': json.loads(row['transformations']) if isinstance(row['transformations'], str) else row['transformations'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
        'created_by': row['created_by']
    }


def _format_model_row(row: dict, company_id: str) -> Dict[str, Any]:
    """Convert database row to properly formatted model dict"""
    return {
        'model_id': str(row['model_id']),
        'company_id': company_id,
        'equipment_id': str(row['equipment_id']) if row.get('equipment_id') else None,
        'equipment_type': row.get('equipment_type'),
        'model_name': row['model_name'],
        'description': row.get('description'),
        'model_type': row['model_type'],
        'model_version': str(row['model_version']) if row.get('model_version') is not None else None,
        'mlflow_run_id': row.get('mlflow_run_id'),
        'mlflow_experiment_id': row.get('mlflow_experiment_id'),
        'model_path': row.get('model_path'),
        'training_metrics': json.loads(row['training_metrics']) if isinstance(row.get('training_metrics'), str) else row.get('training_metrics'),
        'hyperparameters': json.loads(row['hyperparameters']) if isinstance(row.get('hyperparameters'), str) else row.get('hyperparameters'),
        'feature_names': row.get('feature_names'),
        'feature_config_id': str(row['feature_config_id']) if row.get('feature_config_id') else None,
        'sensor_ids': [str(sid) for sid in row['sensor_ids']] if row.get('sensor_ids') else None,
        'accuracy': row.get('accuracy'),
        'precision_score': row.get('precision_score'),
        'recall': row.get('recall'),
        'f1_score': row.get('f1_score'),
        'auc_roc': row.get('auc_roc'),
        'training_samples': row.get('training_samples'),
        'training_start_date': row.get('training_start_date'),
        'training_end_date': row.get('training_end_date'),
        'reference_profile': json.loads(row['reference_profile']) if isinstance(row.get('reference_profile'), str) else row.get('reference_profile'),
        'status': row['status'],
        'deployed_at': row.get('deployed_at'),
        'deprecated_at': row.get('deprecated_at'),
        'created_at': row['created_at'],
        'updated_at': row['updated_at']
    }


def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Convert a company_id UUID to its tenant schema name.

    company_id is validated as a UUID before the schema name is built, so the result is
    always 'tenant_' followed by hex/underscore characters and is safe to interpolate
    into a SET search_path statement. A non-UUID value raises ValueError instead of
    reaching SQL (defends against injection — see ADR-0003).
    Example: 550e8400-e29b-41d4-a716-446655440000 -> tenant_550e8400_e29b_41d4_a716_446655440000
    """
    canonical = str(uuid.UUID(str(company_id)))
    return f"tenant_{canonical.replace('-', '_')}"


class MLRepository:
    """Repository for ML models in industryflow database"""
    
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def get_all_models(
        self, 
        company_id: str,
        status: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """Get all models for a company from tenant schema"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            query = """
                SELECT
                    model_id, equipment_id, equipment_type, model_name, description, model_type,
                    model_version, mlflow_run_id, mlflow_experiment_id, model_path,
                    training_metrics, hyperparameters, feature_names, feature_config_id, sensor_ids,
                    accuracy, precision_score, recall, f1_score, auc_roc,
                    training_samples, training_start_date, training_end_date,
                    status, deployed_at, deprecated_at, created_at, updated_at
                FROM ml_models
            """

            params = []
            if status:
                query += " WHERE status = $1"
                params.append(status)

            query += f" ORDER BY created_at DESC LIMIT ${len(params) + 1}"
            params.append(limit)

            rows = await conn.fetch(query, *params)

            # Format rows with proper type conversions
            return [
                _format_model_row(dict(row), company_id)
                for row in rows
            ]

    async def create_model(
        self,
        company_id: str,
        model_data: Dict[str, Any]
    ) -> Optional[str]:
        """Create a new model entry"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            # Build INSERT query dynamically based on provided fields
            # Note: company_id is NOT in table, it's implied by schema
            fields = []
            values = []
            placeholders = []

            field_mapping = {
                'equipment_id': 'equipment_id',
                'equipment_type': 'equipment_type',
                'model_name': 'model_name',
                'model_version': 'model_version',
                'model_type': 'model_type',
                'model_path': 'model_path',
                'status': 'status',
                'mlflow_run_id': 'mlflow_run_id',
                'mlflow_experiment_id': 'mlflow_experiment_id',
                'accuracy': 'accuracy',
                'precision_score': 'precision_score',
                'recall': 'recall',
                'f1_score': 'f1_score',
                'auc_roc': 'auc_roc',
                'training_metrics': 'training_metrics',
                'hyperparameters': 'hyperparameters',
                'feature_names': 'feature_names',
                'feature_config_id': 'feature_config_id',
                'sensor_ids': 'sensor_ids',
                'training_samples': 'training_samples',
                'training_start_date': 'training_start_date',
                'training_end_date': 'training_end_date',
                'training_duration_seconds': 'training_duration_seconds',
                'reference_profile': 'reference_profile'  # ADR-0021 drift baseline
            }

            # JSONB fields that need serialization (note: feature_names and sensor_ids are arrays, not JSONB)
            jsonb_fields = {'training_metrics', 'hyperparameters', 'reference_profile'}

            for key, db_field in field_mapping.items():
                if key in model_data and model_data[key] is not None:
                    fields.append(db_field)
                    value = model_data[key]

                    # Convert to proper types for database
                    if key == 'model_version' and isinstance(value, str):
                        # model_version DB column is integer, extract major version
                        try:
                            value = int(value.split('.')[0])
                        except (ValueError, AttributeError):
                            value = 1  # Default to version 1

                    # Serialize JSONB fields
                    elif key in jsonb_fields and isinstance(value, (dict, list)):
                        value = json.dumps(value)

                    values.append(value)
                    placeholders.append(f'${len(values)}')

            query = f"""
                INSERT INTO ml_models ({', '.join(fields)})
                VALUES ({', '.join(placeholders)})
                RETURNING model_id
            """

            try:
                logger.info(f"Inserting model with fields: {fields}")
                logger.info(f"Values: {values}")
                logger.info(f"Query: {query}")
                model_id = await conn.fetchval(query, *values)
                logger.info(f"Model created: {model_id} in schema {schema_name}")
                return str(model_id)
            except Exception as e:
                logger.error(f"Failed to create model: {e}")
                logger.error(f"Fields were: {fields}")
                logger.error(f"Values were: {values}")
                return None

    async def get_model_by_id(
        self,
        company_id: str,
        model_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get single model by ID"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            row = await conn.fetchrow(
                """
                SELECT
                    model_id, equipment_id, equipment_type, model_name, description, model_type,
                    model_version, mlflow_run_id, mlflow_experiment_id, model_path,
                    training_metrics, hyperparameters, feature_names, feature_config_id, sensor_ids,
                    accuracy, precision_score, recall, f1_score, auc_roc,
                    training_samples, training_start_date, training_end_date,
                    reference_profile,
                    status, deployed_at, deprecated_at, created_at, updated_at
                FROM ml_models
                WHERE model_id = $1
                """,
                model_id
            )

            if not row:
                return None

            return _format_model_row(dict(row), company_id)

    async def read_sensor_window(
        self,
        company_id: str,
        sensor_ids: List[str],
        start,
        end,
    ) -> Dict[str, List[float]]:
        """Read a trailing window of raw sensor values, grouped by sensor name (ADR-0021).

        Returns ``{sensor_name: [values...]}`` for the drift evaluator to compare against
        the model's reference profile. Tenant-scoped via search_path (ADR-0003); the drift
        endpoint reads only its own tenant's data.
        """
        if not sensor_ids:
            return {}

        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            rows = await conn.fetch(
                """
                SELECT s.sensor_name AS sensor_name, m.value AS value
                FROM sensor_measurements m
                JOIN sensors s ON s.sensor_id = m.sensor_id
                WHERE m.sensor_id = ANY($1::uuid[])
                  AND m.time >= $2
                  AND m.time < $3
                ORDER BY m.time
                """,
                sensor_ids, start, end,
            )

        window: Dict[str, List[float]] = {}
        for row in rows:
            window.setdefault(row['sensor_name'], []).append(row['value'])
        return window
    
    async def update_model_status(
        self,
        company_id: str,
        model_id: str,
        status: str
    ) -> bool:
        """Update model status"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            result = await conn.execute(
                "UPDATE ml_models SET status = $1 WHERE model_id = $2",
                status,
                model_id
            )
            
            return result == "UPDATE 1"
    
    async def delete_model(
        self,
        company_id: str,
        model_id: str
    ) -> bool:
        """Archive model (soft delete)"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            result = await conn.execute(
                "UPDATE ml_models SET status = 'archived' WHERE model_id = $1",
                model_id
            )
            
            return result == "UPDATE 1"
    
    async def get_latest_model(
        self,
        company_id: str,
        model_type: str
    ) -> Optional[Dict[str, Any]]:
        """Get latest active model for a type"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            row = await conn.fetchrow(
                """
                SELECT 
                    model_id, model_name, model_type, model_path,
                    accuracy, created_at
                FROM ml_models
                WHERE status IN ('active', 'production')
                    AND model_type = $1
                ORDER BY created_at DESC
                LIMIT 1
                """,
                model_type
            )
            
            if not row:
                return None
            
            return {**dict(row), 'company_id': company_id}
    
    async def compare_models(
        self,
        company_id: str,
        model_ids: List[str]
    ) -> List[Dict[str, Any]]:
        """Compare multiple models"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            rows = await conn.fetch(
                """
                SELECT 
                    model_id, model_name, model_type, accuracy,
                    precision_score, recall, f1_score, training_start_date,
                    model_version
                FROM ml_models
                WHERE model_id = ANY($1)
                ORDER BY created_at DESC
                """,
                model_ids
            )
            
            return [
                {**dict(row), 'company_id': company_id}
                for row in rows
            ]

    # =========================================================================
    # FEATURE ENGINEERING CONFIGURATION METHODS
    # =========================================================================

    async def create_feature_config(
        self,
        company_id: str,
        equipment_type: str,
        name: str,
        base_sensors: List[str],
        transformations: List[Dict[str, Any]],
        description: Optional[str] = None,
        created_by: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new feature engineering configuration"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            row = await conn.fetchrow(
                """
                INSERT INTO feature_engineering_configs (
                    equipment_type, name, description,
                    base_sensors, transformations, created_by
                )
                VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)
                RETURNING
                    id, equipment_type, name, description,
                    base_sensors, transformations,
                    created_at, updated_at, created_by
                """,
                equipment_type, name, description,
                json.dumps(base_sensors), json.dumps(transformations), created_by
            )

            return _format_feature_config_row(dict(row), company_id)

    async def get_feature_config_by_id(
        self,
        company_id: str,
        config_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get feature config by ID"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            row = await conn.fetchrow(
                """
                SELECT
                    id, equipment_type, name, description,
                    base_sensors, transformations,
                    created_at, updated_at, created_by
                FROM feature_engineering_configs
                WHERE id = $1
                """,
                config_id
            )

            if not row:
                return None

            return _format_feature_config_row(dict(row), company_id)

    async def get_feature_configs_by_equipment_type(
        self,
        company_id: str,
        equipment_type: str
    ) -> List[Dict[str, Any]]:
        """Get all feature configs for an equipment type"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            rows = await conn.fetch(
                """
                SELECT
                    id, equipment_type, name, description,
                    base_sensors, transformations,
                    created_at, updated_at, created_by
                FROM feature_engineering_configs
                WHERE equipment_type = $1
                ORDER BY created_at DESC
                """,
                equipment_type
            )

            return [
                _format_feature_config_row(dict(row), company_id)
                for row in rows
            ]

    async def get_all_feature_configs(
        self,
        company_id: str,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Get all feature configs for a company"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            rows = await conn.fetch(
                """
                SELECT
                    id, equipment_type, name, description,
                    base_sensors, transformations,
                    created_at, updated_at, created_by
                FROM feature_engineering_configs
                ORDER BY created_at DESC
                LIMIT $1
                """,
                limit
            )

            return [
                _format_feature_config_row(dict(row), company_id)
                for row in rows
            ]

    async def update_feature_config(
        self,
        company_id: str,
        config_id: str,
        equipment_type: Optional[str] = None,
        name: Optional[str] = None,
        description: Optional[str] = None,
        base_sensors: Optional[List[str]] = None,
        transformations: Optional[List[Dict[str, Any]]] = None
    ) -> Optional[Dict[str, Any]]:
        """Update feature config"""
        schema_name = normalize_company_id_to_schema(company_id)

        # Build dynamic update query
        updates = []
        params = [config_id]
        param_count = 2

        if equipment_type is not None:
            updates.append(f"equipment_type = ${param_count}")
            params.append(equipment_type)
            param_count += 1

        if name is not None:
            updates.append(f"name = ${param_count}")
            params.append(name)
            param_count += 1

        if description is not None:
            updates.append(f"description = ${param_count}")
            params.append(description)
            param_count += 1

        if base_sensors is not None:
            updates.append(f"base_sensors = ${param_count}::jsonb")
            params.append(json.dumps(base_sensors))
            param_count += 1

        if transformations is not None:
            updates.append(f"transformations = ${param_count}::jsonb")
            params.append(json.dumps(transformations))
            param_count += 1

        if not updates:
            return await self.get_feature_config_by_id(company_id, config_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            query = f"""
                UPDATE feature_engineering_configs
                SET {', '.join(updates)}
                WHERE id = $1
                RETURNING
                    id, equipment_type, name, description,
                    base_sensors, transformations,
                    created_at, updated_at, created_by
            """

            row = await conn.fetchrow(query, *params)

            if not row:
                return None

            return _format_feature_config_row(dict(row), company_id)

    async def delete_feature_config(
        self,
        company_id: str,
        config_id: str
    ) -> bool:
        """Delete feature config"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")

            result = await conn.execute(
                "DELETE FROM feature_engineering_configs WHERE id = $1",
                config_id
            )

            return result == "DELETE 1"
