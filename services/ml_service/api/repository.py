"""
Repository Layer - Schema-per-tenant Data Access
Handles queries to both industryflow and mlflow databases
"""
import asyncpg
import logging
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


def normalize_company_id_to_schema(company_id: str) -> str:
    """
    Convert company_id UUID to schema name
    Example: 550e8400-e29b-41d4-a716-446655440000 -> tenant_550e8400_e29b_41d4_a716_446655440000
    """
    clean_id = company_id.replace('-', '_')
    return f"tenant_{clean_id}"


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
                    model_id, equipment_id, model_name, description, model_type,
                    model_version, mlflow_run_id, mlflow_experiment_id, model_path,
                    training_metrics, hyperparameters, feature_names, sensor_ids,
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
            
            # Inject company_id into response
            return [
                {**dict(row), 'company_id': company_id}
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
                'sensor_ids': 'sensor_ids',
                'training_samples': 'training_samples',
                'training_start_date': 'training_start_date',
                'training_end_date': 'training_end_date',
                'training_duration_seconds': 'training_duration_seconds'
            }

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
                    model_id, equipment_id, model_name, description, model_type,
                    model_version, mlflow_run_id, mlflow_experiment_id, model_path,
                    training_metrics, hyperparameters, feature_names, sensor_ids,
                    accuracy, precision_score, recall, f1_score, auc_roc,
                    training_samples, training_start_date, training_end_date,
                    status, deployed_at, deprecated_at, created_at, updated_at
                FROM ml_models
                WHERE model_id = $1
                """,
                model_id
            )
            
            if not row:
                return None
            
            return {**dict(row), 'company_id': company_id}
    
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


class MLflowRepository:
    """Repository for MLflow data in mlflow database"""
    
    def __init__(self, pool: asyncpg.Pool):
        self.pool = pool
    
    async def get_all_experiments(
        self,
        company_id: str
    ) -> List[Dict[str, Any]]:
        """Get all experiments for a company"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            rows = await conn.fetch(
                """
                SELECT 
                    experiment_id, name, artifact_location, lifecycle_stage
                FROM experiments
                WHERE lifecycle_stage = 'active'
                ORDER BY experiment_id DESC
                """
            )
            
            return [dict(row) for row in rows]
    
    async def get_experiment_by_id(
        self,
        company_id: str,
        experiment_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get experiment details"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            row = await conn.fetchrow(
                """
                SELECT 
                    experiment_id, name, artifact_location, lifecycle_stage,
                    creation_time, last_update_time
                FROM experiments
                WHERE experiment_id = $1
                """,
                int(experiment_id)
            )
            
            if not row:
                return None
            
            return dict(row)
    
    async def get_runs_for_experiment(
        self,
        company_id: str,
        experiment_id: str,
        max_results: int = 100
    ) -> List[Dict[str, Any]]:
        """Get runs for an experiment"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            # Verify experiment exists
            exp_exists = await conn.fetchval(
                "SELECT 1 FROM experiments WHERE experiment_id = $1",
                int(experiment_id)
            )
            
            if not exp_exists:
                return []
            
            rows = await conn.fetch(
                """
                SELECT 
                    run_uuid as run_id,
                    experiment_id, status, start_time, end_time, artifact_uri
                FROM runs
                WHERE experiment_id = $1
                ORDER BY start_time DESC
                LIMIT $2
                """,
                int(experiment_id),
                max_results
            )
            
            return [dict(row) for row in rows]
    
    async def get_run_details(
        self,
        company_id: str,
        run_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get complete run details with metrics, params, tags"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            # Get run info
            row = await conn.fetchrow(
                """
                SELECT 
                    run_uuid as run_id, experiment_id, status,
                    start_time, end_time, artifact_uri, lifecycle_stage
                FROM runs
                WHERE run_uuid = $1
                """,
                run_id
            )
            
            if not row:
                return None
            
            result = dict(row)
            
            # Get metrics
            metrics_rows = await conn.fetch(
                """
                SELECT key, value, timestamp, step
                FROM metrics
                WHERE run_uuid = $1
                ORDER BY timestamp
                """,
                run_id
            )
            result['metrics'] = {r['key']: r['value'] for r in metrics_rows}
            
            # Get params
            params_rows = await conn.fetch(
                "SELECT key, value FROM params WHERE run_uuid = $1",
                run_id
            )
            result['params'] = {r['key']: r['value'] for r in params_rows}
            
            # Get tags
            tags_rows = await conn.fetch(
                "SELECT key, value FROM tags WHERE run_uuid = $1",
                run_id
            )
            result['tags'] = {r['key']: r['value'] for r in tags_rows}
            
            return result
    
    async def get_registered_models(
        self,
        company_id: str
    ) -> List[Dict[str, Any]]:
        """Get all registered models"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            rows = await conn.fetch(
                """
                SELECT 
                    name, creation_timestamp, last_updated_timestamp, description
                FROM registered_models
                ORDER BY last_updated_timestamp DESC
                """
            )
            
            return [dict(row) for row in rows]
    
    async def get_registered_model_with_versions(
        self,
        company_id: str,
        model_name: str
    ) -> Optional[Dict[str, Any]]:
        """Get registered model with all versions"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self.pool.acquire() as conn:
            await conn.execute(f"SET search_path TO {schema_name}, public")
            
            # Get model
            row = await conn.fetchrow(
                """
                SELECT 
                    name, creation_timestamp, last_updated_timestamp, description
                FROM registered_models
                WHERE name = $1
                """,
                model_name
            )
            
            if not row:
                return None
            
            result = dict(row)
            
            # Get versions
            versions_rows = await conn.fetch(
                """
                SELECT 
                    version, current_stage, run_id, status, creation_timestamp
                FROM model_versions
                WHERE name = $1
                ORDER BY version DESC
                """,
                model_name
            )
            
            result['versions'] = [dict(v) for v in versions_rows]
            
            return result
