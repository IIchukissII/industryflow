# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Repository layer for Alert Service Worker
Schema-per-tenant architecture
"""
import asyncpg
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
import uuid as uuid_module
import logging

from industryflow_contracts import tenancy

logger = logging.getLogger(__name__)


# The one company_id -> schema mapping (ADR-0003 dec 5, 7), under the name this module's callers use.
# It validates company_id as a UUID first, so a non-UUID raises ValueError instead of reaching SQL.
# This path is reachable from untrusted Kafka payloads, so the validation is load-bearing.
normalize_company_id_to_schema = tenancy.schema_name


# Dedup scopes for save_alert_once ($n are the insert's own parameters). An alert's scope is
# (rule, sensor, equipment); a retrain recommendation's is its model. The two never suppress each other.
_ALERT_SCOPE = (
    "rule_id IS NOT DISTINCT FROM $3::uuid AND sensor_id IS NOT DISTINCT FROM $4::uuid"
    " AND equipment_id IS NOT DISTINCT FROM $5::uuid"
    " AND condition IS DISTINCT FROM 'retrain_recommended'"
)
_RETRAIN_SCOPE = "model_id = $11::uuid AND condition = 'retrain_recommended'"

_SAVE_ALERT_ONCE = """
    WITH prior AS (
        SELECT max(triggered_at) AS prior_at FROM alerts
        WHERE {scope}
          AND triggered_at > $2::timestamptz - $15::interval
          AND triggered_at < $2::timestamptz + $15::interval
    ), ins AS (
        INSERT INTO alerts (
            alert_id, triggered_at, rule_id, sensor_id, equipment_id, site_id, detection_type,
            threshold_value, actual_value, condition, model_id, anomaly_score, severity, message,
            acknowledged
        )
        SELECT $1::uuid, $2::timestamptz, $3::uuid, $4::uuid, $5::uuid, $6::text, $7::text,
               $8::float8, $9::float8, $10::text, $11::uuid, $12::float8, $13::text, $14::text, false
        WHERE (SELECT prior_at FROM prior) IS NULL
        ON CONFLICT (alert_id, triggered_at) DO NOTHING
        RETURNING alert_id
    )
    SELECT (SELECT alert_id FROM ins) AS inserted, (SELECT prior_at FROM prior) AS prior_at
"""


def dedup_lock_key(schema_name: str, scope: Tuple) -> int:
    """64-bit advisory-lock key for one dedup scope in one tenant schema.

    Two writers in the same scope (two replicas' ML passes, or a redelivery racing the original)
    serialise on it, so the window check and the insert cannot interleave. A hash collision
    between scopes only serialises two unrelated inserts; it never suppresses one.
    """
    name = schema_name + '|' + '|'.join('' if part is None else str(part) for part in scope)
    return int.from_bytes(hashlib.blake2b(name.encode(), digest_size=8).digest(), 'big', signed=True)


class AlertRepository:
    """Repository for alert operations with schema-per-tenant"""

    def __init__(self, db_pool: asyncpg.Pool):
        self.pool = db_pool

    @asynccontextmanager
    async def _tenant(self, schema_name: str):
        """A pooled connection inside a transaction whose search_path is the tenant's.

        SET LOCAL ends with the transaction (ADR-0003 dec 6), so the connection goes back to the
        pool without a tenant path, whether the block commits or raises.
        """
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path TO "{schema_name}", public')
                yield conn

    async def get_active_rules(self, company_id: str) -> List[Dict]:
        """Get active alert rules for a tenant"""
        schema_name = normalize_company_id_to_schema(company_id)
        
        async with self._tenant(schema_name) as conn:
            
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
    
    async def save_alert_once(
        self, alert_data: Dict, *, window: timedelta, retrain: bool = False,
    ) -> Tuple[bool, Optional[datetime]]:
        """
        Insert the alert unless one in the same dedup scope already exists within window of its
        event time, in one statement. Returns (inserted, prior_at), where prior_at is the event time
        of the alert that suppressed it.

        The window check makes the cooldown durable across restarts; ON CONFLICT on the
        deterministic (alert_id, triggered_at) makes an exact replay a no-op even if two consumers
        race (ADR-0005 dec 6). Needs no schema change: the window lookup is bounded by the
        hypertable's time index.

        Two inserts in the same scope with different event times (two replicas, or a redelivery
        racing the original) would each find no prior row under READ COMMITTED and both insert.
        A transaction-scoped advisory lock on the scope serialises them: the second statement
        starts after the first commits, so its window check sees the first row. That is what
        makes the cooldown hold across replicas, not only across restarts.
        """
        schema_name = normalize_company_id_to_schema(alert_data['company_id'])
        if retrain:
            scope_sql = _RETRAIN_SCOPE
            scope = ('retrain', alert_data.get('model_id'))
        else:
            scope_sql = _ALERT_SCOPE
            scope = ('alert', alert_data['rule_id'], alert_data.get('sensor_id'), alert_data.get('equipment_id'))

        async with self._tenant(schema_name) as conn:
            await conn.execute("SELECT pg_advisory_xact_lock($1)", dedup_lock_key(schema_name, scope))
            row = await conn.fetchrow(
                _SAVE_ALERT_ONCE.format(scope=scope_sql),
                alert_data['alert_id'],
                alert_data['triggered_at'],
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
                window,
            )

        inserted = row['inserted'] is not None
        if inserted:
            logger.info(f"Saved alert {alert_data['alert_id']} to {schema_name}")
        return inserted, row['prior_at']

    async def get_tenant_schemas(self) -> List[str]:
        """Get all tenant schemas: the catalog's candidates that are exactly a tenant's schema name."""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT schema_name
                FROM information_schema.schemata
                WHERE schema_name LIKE $1
                ORDER BY schema_name
            """, tenancy.SCHEMA_LIKE)
        schemas = []
        for row in rows:
            try:
                tenancy.company_id_from_schema(row['schema_name'])
            except ValueError:
                continue
            schemas.append(row['schema_name'])
        return schemas

    async def get_company_id_from_schema(self, schema_name: str) -> str:
        """The company_id a tenant schema was built from (the inverse of the canonical mapping)."""
        return tenancy.company_id_from_schema(schema_name)

    async def get_sensor_name(self, sensor_id: str, company_id: str) -> Optional[str]:
        """Get sensor name from sensor UUID"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self._tenant(schema_name) as conn:

            # Query sensor name
            sensor_name = await conn.fetchval(
                "SELECT sensor_name FROM sensors WHERE sensor_id = $1",
                uuid_module.UUID(sensor_id)
            )

            return sensor_name

    async def get_feature_config(self, config_id: str, company_id: str) -> Optional[Dict]:
        """Get feature engineering config by ID"""
        schema_name = normalize_company_id_to_schema(company_id)

        async with self._tenant(schema_name) as conn:

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

        async with self._tenant(schema_name) as conn:

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

    async def get_retrain_signals(
        self,
        model_id: str,
        company_id: str,
        precision_window_days: int,
        drift_lookback_hours: int,
    ) -> Dict:
        """Read the two signals the retrain recommendation combines (ADR-0022 dec 4).

        - operator-label precision counts (tp/fp) for this model over the precision window,
          from the durable alert_labels store (ADR-0022 slice 1);
        - the count of recent drift alerts for this model (sustained drift) from alert history.

        Both tenant-scoped via search_path. Returns {tp, fp, recent_drift_alerts}.
        """
        schema_name = normalize_company_id_to_schema(company_id)
        model_uuid = uuid_module.UUID(str(model_id))

        async with self._tenant(schema_name) as conn:

            label_row = await conn.fetchrow(
                """
                SELECT
                    count(*) FILTER (WHERE verdict = 'true_positive')  AS tp,
                    count(*) FILTER (WHERE verdict = 'false_positive') AS fp
                FROM alert_labels
                WHERE model_id = $1
                  AND labeled_at >= NOW() - make_interval(days => $2::int)
                """,
                model_uuid, precision_window_days,
            )
            recent_drift = await conn.fetchval(
                """
                SELECT count(*)
                FROM alerts
                WHERE model_id = $1
                  AND detection_type = 'statistical'
                  AND condition = 'drift'
                  AND triggered_at >= NOW() - make_interval(hours => $2::int)
                """,
                model_uuid, drift_lookback_hours,
            )

        return {
            "tp": (label_row["tp"] if label_row else 0) or 0,
            "fp": (label_row["fp"] if label_row else 0) or 0,
            "recent_drift_alerts": recent_drift or 0,
        }


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
