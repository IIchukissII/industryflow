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
from contextlib import asynccontextmanager
from typing import List, Dict, Any, Optional

from industryflow_contracts import tenancy


# The models a feature config is bound by (ADR-0041). A constant so the database proof runs this exact
# text (infrastructure/timescaledb/tests/test_bound_feature_config.py).
# The configuration a registration binds, locked for the insert (ADR-0041 dec 3): an edit that committed
# after the registration validated it is seen here and refused, and one that comes later waits.
BOUND_CONFIG_LOCK = "SELECT base_sensors, transformations FROM feature_engineering_configs WHERE id = $1 FOR SHARE"

BOUND_MODELS_SELECT = "SELECT model_id, model_name FROM ml_models WHERE feature_config_id = $1 ORDER BY model_name"

logger = logging.getLogger(__name__)


class FeatureConfigChanged(Exception):
    """The feature configuration a registration validated changed before the model was bound to it."""


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
        # The baseline without its sample: what the list read carries to say whether a model can be
        # monitored (ADR-0035 dec 4), without moving every model's sample.
        'baseline_summary': json.loads(row['baseline_summary']) if isinstance(row.get('baseline_summary'), str) else row.get('baseline_summary'),
        # ADR-0027. Absent from the list query's older callers, hence .get() — and None is a real
        # verdict ("never evaluated"), not a formatting artefact.
        'compatibility_status': row.get('compatibility_status'),
        'compatibility_detail': json.loads(row['compatibility_detail']) if isinstance(row.get('compatibility_detail'), str) else row.get('compatibility_detail'),
        'compatibility_checked_at': row.get('compatibility_checked_at'),
        # ADR-0030 dec 8. Read as well as written: the deployment gate asks an uploaded model where
        # its artifact IS, because it has no run to answer that. A column the write path stores and
        # the read path drops is a column that does not exist — which is how this surfaced, as a
        # KeyError on a real box after every unit test had passed.
        'provenance': row.get('provenance'),
        'artifact_uri': row.get('artifact_uri'),
        # ADR-0010 dec 6 / ADR-0028 dec 1: the detector this model is scored by — the declaration of
        # what its output means. Inference dispatches on it, so a read that drops it would leave
        # every model scored by whatever default the caller assumed.
        'detector': row.get('detector'),
        'status': row['status'],
        'deployed_at': row.get('deployed_at'),
        'deprecated_at': row.get('deprecated_at'),
        'created_at': row['created_at'],
        'updated_at': row['updated_at']
    }


# The one company_id -> schema mapping (ADR-0003 dec 5, 7), under the name this service uses. It
# validates company_id as a UUID before the name is built, so a non-UUID raises ValueError instead
# of reaching SQL.
normalize_company_id_to_schema = tenancy.schema_name


@asynccontextmanager
async def tenant_connection(pool: asyncpg.Pool, company_id: str):
    """A pooled connection whose tenant ``search_path`` cannot outlive this block (ADR-0003 dec 6).

    The path is applied with transaction-local scope inside a transaction that spans the block, so
    it is discarded when the transaction ends and a connection never returns to the pool carrying
    this tenant's path. A session-scoped ``SET`` would leave it there for the next borrower, and
    isolation would then rest on every later query remembering to set its own path first.
    ``set_config(..., true)`` is ``SET LOCAL`` with the value bound as a parameter.
    """
    schema_name = normalize_company_id_to_schema(company_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('search_path', $1, true)", f"{schema_name}, public"
            )
            yield conn


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
        
        async with tenant_connection(self.pool, company_id) as conn:
            
            query = """
                SELECT
                    model_id, equipment_id, equipment_type, model_name, description, model_type,
                    model_version, mlflow_run_id, mlflow_experiment_id, model_path,
                    training_metrics, hyperparameters, feature_names, feature_config_id, sensor_ids,
                    accuracy, precision_score, recall, f1_score, auc_roc,
                    training_samples, training_start_date, training_end_date,
                    compatibility_status, compatibility_detail, compatibility_checked_at,
                    provenance, artifact_uri, detector,
                    status, deployed_at, deprecated_at, created_at, updated_at,
                    reference_profile - 'sample' AS baseline_summary
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
        model_data: Dict[str, Any],
        *,
        bound_inputs: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """Create a new model entry.

        ``bound_inputs`` is the feature configuration's ``base_sensors`` and ``transformations`` as the
        registration validated them; the row is locked and compared in the insert's transaction, and
        ``FeatureConfigChanged`` is raised when it no longer says that (ADR-0041 dec 3).
        """
        schema_name = normalize_company_id_to_schema(company_id)

        async with tenant_connection(self.pool, company_id) as conn:

            if bound_inputs is not None and model_data.get('feature_config_id'):
                row = await conn.fetchrow(BOUND_CONFIG_LOCK, model_data['feature_config_id'])

                def _value(v):
                    return json.loads(v) if isinstance(v, str) else v

                if row is None or {k: _value(row[k]) for k in ('base_sensors', 'transformations')} != {
                        k: bound_inputs.get(k) for k in ('base_sensors', 'transformations')}:
                    raise FeatureConfigChanged(model_data['feature_config_id'])

            # Build INSERT query dynamically based on provided fields
            # Note: company_id is NOT in table, it's implied by schema
            fields = []
            values = []
            placeholders = []

            field_mapping = {
                'equipment_id': 'equipment_id',
                'equipment_type': 'equipment_type',
                'model_name': 'model_name',
                'description': 'description',
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
                'reference_profile': 'reference_profile',  # ADR-0021 drift baseline
                # ADR-0027: the verdict on whether this serving environment can honour what the
                # artifact declares. Set by the registration gate, refreshed at the deployment gate.
                'compatibility_status': 'compatibility_status',
                'compatibility_detail': 'compatibility_detail',
                'compatibility_checked_at': 'compatibility_checked_at',
                # ADR-0030 dec 8: where the model came from, and — for one the platform never
                # watched being made — where its artifact is, since it has no run to answer that
                # (declining to invent a run means recording what the run would have told us).
                'provenance': 'provenance',
                'artifact_uri': 'artifact_uri',
                # ADR-0010 dec 6 / ADR-0028 dec 1: which detector scores this model, chosen at the
                # registration gate (for an upload, the one that implements its declared semantics).
                'detector': 'detector',
            }

            # JSONB fields that need serialization (note: feature_names and sensor_ids are arrays, not JSONB)
            jsonb_fields = {'training_metrics', 'hyperparameters', 'reference_profile',
                            'compatibility_detail'}

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
            except asyncpg.UniqueViolationError:
                # This name and version already exist. That is the caller's fact, not a fault of
                # ours: reporting it as a server error tells them to retry something that will never
                # work, and buries the one sentence they need. Raised rather than swallowed so the
                # router can say which it is.
                logger.info(f"Model {model_data.get('model_name')} v{model_data.get('model_version')} already exists")
                raise
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

        async with tenant_connection(self.pool, company_id) as conn:

            row = await conn.fetchrow(
                """
                SELECT
                    model_id, equipment_id, equipment_type, model_name, description, model_type,
                    model_version, mlflow_run_id, mlflow_experiment_id, model_path,
                    training_metrics, hyperparameters, feature_names, feature_config_id, sensor_ids,
                    accuracy, precision_score, recall, f1_score, auc_roc,
                    training_samples, training_start_date, training_end_date,
                    reference_profile,
                    compatibility_status, compatibility_detail, compatibility_checked_at,
                    provenance, artifact_uri, detector,
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
    ) -> List[Dict[str, Any]]:
        """Read a trailing window of raw sensor readings, oldest first (ADR-0021).

        Returns one ``{time, sensor_name, value}`` per reading. The time is kept because the drift
        evaluator compares ROWS, and a row is the set of readings taken at one instant: grouping
        values per sensor without their times loses the alignment, and sensors that report at
        different rates then no longer line up. Tenant-scoped via search_path (ADR-0003); the
        drift endpoint reads only its own tenant's data.
        """
        if not sensor_ids:
            return []

        async with tenant_connection(self.pool, company_id) as conn:
            rows = await conn.fetch(
                """
                SELECT m.time AS time, s.sensor_name AS sensor_name, m.value AS value
                FROM sensor_measurements m
                JOIN sensors s ON s.sensor_id = m.sensor_id
                WHERE m.sensor_id = ANY($1::uuid[])
                  AND m.time >= $2
                  AND m.time < $3
                ORDER BY m.time
                """,
                sensor_ids, start, end,
            )

        return [
            {'time': row['time'], 'sensor_name': row['sensor_name'], 'value': row['value']}
            for row in rows
        ]
    
    async def update_model_status(
        self,
        company_id: str,
        model_id: str,
        status: str
    ) -> bool:
        """Update model status"""

        async with tenant_connection(self.pool, company_id) as conn:

            result = await conn.execute(
                "UPDATE ml_models SET status = $1 WHERE model_id = $2",
                status,
                model_id
            )

            return result == "UPDATE 1"

    async def update_model_reference_profile(
        self,
        company_id: str,
        model_id: str,
        profile: Dict[str, Any],
    ) -> bool:
        """Record the baseline the deployment gate derived (ADR-0035 dec 2, 3)."""

        async with tenant_connection(self.pool, company_id) as conn:
            result = await conn.execute(
                "UPDATE ml_models SET reference_profile = $1 WHERE model_id = $2",
                json.dumps(profile),
                model_id,
            )
            return result == "UPDATE 1"

    async def sensor_ids_by_name(
        self,
        company_id: str,
        equipment_id: str,
        names: List[str],
    ) -> Dict[str, str]:
        """The equipment's sensors with these names, name -> sensor_id; a name it lacks is absent."""

        async with tenant_connection(self.pool, company_id) as conn:
            rows = await conn.fetch(
                "SELECT sensor_name, sensor_id FROM sensors WHERE equipment_id = $1 AND sensor_name = ANY($2::text[])",
                equipment_id,
                names,
            )
            return {r["sensor_name"]: str(r["sensor_id"]) for r in rows}

    async def update_model_compatibility(
        self,
        company_id: str,
        model_id: str,
        compatibility
    ) -> bool:
        """Record the ADR-0027 verdict reached at a gate.

        Re-stamped rather than written once, because the verdict has a shelf life: the serving image
        can be rebuilt underneath a registered model, so what was true at registration need not be true
        at deployment. `compatibility_checked_at` is what makes a stale verdict legible as stale.
        """

        async with tenant_connection(self.pool, company_id) as conn:

            result = await conn.execute(
                """
                UPDATE ml_models
                SET compatibility_status = $1,
                    compatibility_detail = $2,
                    compatibility_checked_at = NOW()
                WHERE model_id = $3
                """,
                compatibility.status,
                json.dumps(compatibility.as_detail()),
                model_id
            )

            return result == "UPDATE 1"
    
    async def delete_model(
        self,
        company_id: str,
        model_id: str
    ) -> bool:
        """Deprecate a model (soft delete) — to `deprecated`, the retire state the ml_models CHECK
        permits; `archived` is an MLflow stage name this table never adopted and the DB rejects it."""

        async with tenant_connection(self.pool, company_id) as conn:

            result = await conn.execute(
                "UPDATE ml_models SET status = 'deprecated' WHERE model_id = $1",
                model_id
            )
            
            return result == "UPDATE 1"
    
    async def get_latest_model(
        self,
        company_id: str,
        model_type: str
    ) -> Optional[Dict[str, Any]]:
        """Get latest active model for a type"""
        
        async with tenant_connection(self.pool, company_id) as conn:
            
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
        
        async with tenant_connection(self.pool, company_id) as conn:
            
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

        async with tenant_connection(self.pool, company_id) as conn:

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

        async with tenant_connection(self.pool, company_id) as conn:

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

        async with tenant_connection(self.pool, company_id) as conn:

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

        async with tenant_connection(self.pool, company_id) as conn:

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

        async with tenant_connection(self.pool, company_id) as conn:

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

        async with tenant_connection(self.pool, company_id) as conn:

            result = await conn.execute(
                "DELETE FROM feature_engineering_configs WHERE id = $1",
                config_id
            )

            return result == "DELETE 1"

    async def models_bound_to_feature_config(
        self,
        company_id: str,
        config_id: str
    ) -> List[Dict[str, Any]]:
        """The models a feature config is bound by (ADR-0041): what keeps it fixed."""

        async with tenant_connection(self.pool, company_id) as conn:

            rows = await conn.fetch(BOUND_MODELS_SELECT, config_id)

            return [{"model_id": str(r["model_id"]), "model_name": r["model_name"]} for r in rows]
