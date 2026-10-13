# SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""
Rules Engine - Evaluates sensor data against alert rules
Schema-per-tenant architecture - UUID-based matching only
"""
import logging
import math
from collections import OrderedDict
from typing import List, Dict, Any, Optional, Tuple
from datetime import datetime, timedelta, timezone
import uuid
import aiohttp

from errors import MessageValidationError

logger = logging.getLogger(__name__)

# Defaults only: the worker passes the configured values in (config.py is the one reader of the
# environment).
DEFAULT_ML_SERVICE_URL = 'http://ml-service-api:8002'
DEFAULT_COOLDOWN_SECONDS = 300.0
DEFAULT_FEATURE_MAX_AGE_SECONDS = 300.0

# Namespace for deterministic alert ids: the same alert key at the same event time always maps to
# the same alert_id, so a redelivered reading collides on the primary key instead of duplicating.
ALERT_ID_NAMESPACE = uuid.UUID('6f1c2a4e-7d3b-5e8a-9c0f-1b2d3e4f5a6b')

# The in-memory dedup state is only a cache in front of the database check, so it is safe to bound.
DEFAULT_DEDUP_CACHE_MAX = 10000


def as_utc(ts: Any) -> datetime:
    """Normalise an event time (ISO string, naive or aware datetime, or None) to aware UTC."""
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
    if ts is None:
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _uuid_field(reading: Dict[str, Any], field: str, *, required: bool) -> Optional[str]:
    raw = reading.get(field)
    if raw is None or raw == '':
        if required:
            raise MessageValidationError(f"reading has no {field}")
        return None
    try:
        return str(uuid.UUID(str(raw)))
    except (ValueError, AttributeError, TypeError):
        raise MessageValidationError(f"{field} is not a UUID: {raw!r}") from None


def parse_reading(sensor_data: Any, company_id: Any) -> Dict[str, Any]:
    """Validate one sensor reading; return it with canonical ids, a float value and UTC times.

    Raises MessageValidationError for anything the engine cannot evaluate, so the consumer
    dead-letters it (ADR-0005 dec 3) instead of committing past it as if it had been handled.
    """
    if not isinstance(sensor_data, dict):
        raise MessageValidationError(f"reading is not an object: {type(sensor_data).__name__}")
    _uuid_field({'company_id': company_id}, 'company_id', required=True)

    reading = dict(sensor_data)
    reading['sensor_id'] = _uuid_field(sensor_data, 'sensor_id', required=True)
    reading['equipment_id'] = _uuid_field(sensor_data, 'equipment_id', required=False)

    value = sensor_data.get('value')
    if value is None or isinstance(value, bool):
        raise MessageValidationError(f"reading has no numeric value: {value!r}")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise MessageValidationError(f"value is not a number: {value!r}") from None
    if not math.isfinite(value):
        raise MessageValidationError(f"value is not finite: {value!r}")
    reading['value'] = value

    for field in ('time', 'timestamp'):
        ts = sensor_data.get(field)
        if ts is None:
            continue
        try:
            reading[field] = as_utc(ts).isoformat()
        except (ValueError, TypeError, AttributeError):
            raise MessageValidationError(f"{field} is not an ISO 8601 time: {ts!r}") from None
    return reading


def deterministic_alert_id(scope: Tuple, triggered_at: datetime) -> str:
    """alert_id derived from the dedup scope and the event time, never random."""
    name = '|'.join('' if part is None else str(part) for part in scope) + '|' + triggered_at.isoformat()
    return str(uuid.uuid5(ALERT_ID_NAMESPACE, name))


def should_recommend_retrain(
    tp: int,
    fp: int,
    recent_drift_alerts: int,
    *,
    precision_floor: float,
    min_labels: int,
    min_drift_alerts: int,
) -> Optional[float]:
    """Decide whether to recommend retraining, from operator-label + drift signals (ADR-0022 dec 4).

    The recommendation requires BOTH conditions together — sustained drift AND label-derived
    precision decay — so neither a drift blip alone nor a couple of bad labels alone triggers it.
    Returns the measured precision when a retrain should be recommended, else None:

      * not enough decided labels (tp + fp < min_labels) → None (insufficient evidence);
      * drift not sustained (recent_drift_alerts < min_drift_alerts) → None;
      * precision still at/above the floor → None (the model is still performing);
      * otherwise → the precision (< floor), i.e. recommend.
    """
    decided = tp + fp
    if decided < min_labels:
        return None
    if recent_drift_alerts < min_drift_alerts:
        return None
    precision = tp / decided
    if precision >= precision_floor:
        return None
    return precision


class RulesEngine:
    """Evaluates sensor data against alert rules with schema-per-tenant"""

    def __init__(
        self,
        tenant_rules: Dict[str, List[Dict]],
        alert_repo,
        feature_store=None,
        *,
        ml_service_url: str = DEFAULT_ML_SERVICE_URL,
        internal_service_token: Optional[str] = None,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
        dedup_cache_max: int = DEFAULT_DEDUP_CACHE_MAX,
        feature_max_age_seconds: Optional[float] = DEFAULT_FEATURE_MAX_AGE_SECONDS,
    ):
        """
        Initialize with rules organized by tenant
        tenant_rules: {company_id: [rules]}
        """
        self.tenant_rules = tenant_rules
        self.alert_repo = alert_repo
        self.feature_store = feature_store
        self.ml_service_url = ml_service_url
        self.internal_service_token = internal_service_token
        self.feature_max_age_seconds = feature_max_age_seconds
        self.sensor_name_cache = {}  # {company_id: {sensor_uuid: sensor_name}}
        # Dedup/cooldown cache: {scope: event time of the last alert known in the DB}. The DB is the
        # source of truth (see _emit); this only spares it a query while an anomaly persists.
        self._last_emit: OrderedDict = OrderedDict()
        self._cooldown_seconds = float(cooldown_seconds)
        self._dedup_cache_max = int(dedup_cache_max)

        # Log loaded rules
        total = sum(len(rules) for rules in tenant_rules.values())
        logger.info(f"Rules engine initialized with {total} rules from {len(tenant_rules)} tenants")
        if feature_store:
            logger.info("Feature Store integration enabled")
    
    def update_rules(self, tenant_rules: Dict[str, List[Dict]]):
        """Update rules (called during periodic reload)"""
        self.tenant_rules = tenant_rules
        total = sum(len(rules) for rules in tenant_rules.values())
        logger.info(f"Rules updated: {total} rules from {len(tenant_rules)} tenants")

    async def _get_sensor_name(self, sensor_id: str, company_id: str) -> str:
        """
        Get sensor name from sensor UUID
        Uses cache to avoid repeated database queries
        """
        # Check cache first
        if company_id in self.sensor_name_cache:
            if sensor_id in self.sensor_name_cache[company_id]:
                return self.sensor_name_cache[company_id][sensor_id]
        else:
            self.sensor_name_cache[company_id] = {}

        # Query database. A failure propagates: a database outage must stall the reading (the
        # consumer retries it), not quietly key it under its UUID where no ML rule looks for it.
        sensor_name = await self.alert_repo.get_sensor_name(sensor_id, company_id)
        if sensor_name:
            self.sensor_name_cache[company_id][sensor_id] = sensor_name
            return sensor_name

        # Fallback: use sensor_id if name not found
        return sensor_id

    def _cached_within(self, scope: Tuple, at: datetime, window: timedelta) -> bool:
        last = self._last_emit.get(scope)
        return last is not None and abs(at - last) < window

    def _remember(self, scope: Tuple, at: datetime) -> None:
        self._last_emit[scope] = at
        self._last_emit.move_to_end(scope)
        while len(self._last_emit) > self._dedup_cache_max:
            self._last_emit.popitem(last=False)

    async def _emit(self, alert: Dict[str, Any], *, retrain: bool = False,
                    cooldown_seconds: Optional[float] = None) -> bool:
        """
        Write the alert unless one with the same scope already exists within the cooldown of its
        event time. Returns True only when a row was written.

        Idempotent against redelivered readings (at-least-once, ADR-0005 dec 6), across restarts:
        the cooldown is checked in the database against event time, and the alert_id is derived
        from scope + event time, so replaying a reading finds its own alert (or collides on the
        primary key) instead of writing a second one. It also stops a persisting anomaly from
        raising an alert on every reading (alert storms).

        Scope: (company, rule, sensor, equipment) for alerts; (company, model) for retrain
        recommendations, which have their own long cooldown (ADR-0022 dec 4).
        """
        if retrain:
            scope = (alert.get('company_id'), 'retrain', alert.get('model_id'))
        else:
            scope = (alert.get('company_id'), alert.get('rule_id'),
                     alert.get('sensor_id'), alert.get('equipment_id'))
        window = timedelta(seconds=self._cooldown_seconds if cooldown_seconds is None else cooldown_seconds)
        at = as_utc(alert.get('triggered_at'))
        alert['triggered_at'] = at
        alert['alert_id'] = deterministic_alert_id(scope, at)

        if self._cached_within(scope, at, window):
            logger.debug(f"Alert suppressed by cooldown (cache): {scope}")
            return False
        inserted, prior_at = await self.alert_repo.save_alert_once(alert, window=window, retrain=retrain)
        if inserted:
            self._remember(scope, at)
            return True
        if prior_at is not None:
            self._remember(scope, as_utc(prior_at))
        logger.debug(f"Alert suppressed by cooldown (db): {scope}")
        return False

    async def evaluate(self, sensor_data: Dict[str, Any], company_id: str) -> List[Dict]:
        """
        Evaluate sensor data against rules for the tenant
        Returns list of triggered alerts

        Raises MessageValidationError for a reading that cannot be evaluated (see parse_reading).
        """
        sensor_data = parse_reading(sensor_data, company_id)
        sensor_id = sensor_data['sensor_id']
        equipment_id = sensor_data['equipment_id']
        value = sensor_data['value']
        timestamp = sensor_data.get('timestamp') or sensor_data.get('time')

        # Store reading in Feature Store for ML inference
        if self.feature_store and equipment_id:
            # Get sensor name from UUID for Feature Store key (a DB failure propagates)
            sensor_name = await self._get_sensor_name(sensor_id, company_id)
            try:
                await self.feature_store.store_reading(
                    company_id=company_id,
                    equipment_id=equipment_id,
                    sensor_name=sensor_name,
                    timestamp=timestamp or datetime.now(timezone.utc).isoformat(),
                    value=value
                )
                logger.debug(f"Stored {sensor_name}={value} in Feature Store for equipment {equipment_id[:8]}...")
            except Exception as e:
                # The snapshot store only feeds the ML rules; a Redis outage must not stop the
                # threshold rules. The ML pass then finds the snapshot stale or incomplete and skips.
                logger.error(f"Failed to store reading in Feature Store: {e}")

        # Get rules for this tenant
        rules = self.tenant_rules.get(company_id, [])
        if not rules:
            logger.debug(f"No rules for company {company_id}")
            return []
        
        # Find applicable rules (UUID-based matching only)
        applicable_rules = self._get_applicable_rules(sensor_id, equipment_id, rules)
        
        if not applicable_rules:
            return []
        
        logger.debug(f"Evaluating {len(applicable_rules)} rules for sensor {sensor_id}")
        
        # Evaluate each rule
        triggered_alerts = []
        
        for rule in applicable_rules:
            alert = await self._evaluate_rule(sensor_data, rule, company_id)
            if alert and await self._emit(alert):
                triggered_alerts.append(alert)

        return triggered_alerts

    async def evaluate_equipment_ml_rules(
        self,
        equipment_id: str,
        company_id: str,
        timestamp: Optional[str] = None
    ) -> List[Dict]:
        """
        Evaluate ML rules for an equipment using complete sensor snapshot

        Args:
            equipment_id: Equipment UUID
            company_id: Company ID
            timestamp: Optional timestamp for the evaluation

        Returns:
            List of triggered ML alerts
        """
        if not self.feature_store:
            logger.warning("Feature Store not available, skipping ML evaluation")
            return []

        # Get ML rules for this equipment
        rules = self.tenant_rules.get(company_id, [])
        logger.info(f"Company {company_id} has {len(rules)} total rules")

        ml_rules = [
            rule for rule in rules
            if rule.get('enabled') and
            rule.get('detection_type') == 'ml' and
            str(rule.get('equipment_id')) == equipment_id
        ]

        logger.info(f"Found {len(ml_rules)} ML rules for equipment {equipment_id[:8]}...")

        if not ml_rules:
            logger.info(f"No ML rules for equipment {equipment_id}")
            return []

        triggered_alerts = []

        for rule in ml_rules:
            try:
                # Get required sensors from feature config
                model_id = rule.get('model_id')
                if not model_id:
                    logger.warning(f"ML rule {rule['rule_id']} has no model_id")
                    continue

                # Get feature config to know which sensors are needed
                feature_config_id = await self._get_feature_config_for_model(model_id, company_id)
                if not feature_config_id:
                    logger.warning(f"No feature config found for model {model_id}")
                    continue

                # Get base sensors from feature config
                feature_config = await self.alert_repo.get_feature_config(str(feature_config_id), str(company_id))
                if not feature_config:
                    logger.warning(f"Feature config {feature_config_id} not found")
                    continue

                base_sensors = feature_config.get('base_sensors', [])
                logger.info(f"base_sensors type: {type(base_sensors)}, length: {len(base_sensors) if hasattr(base_sensors, '__len__') else 'N/A'}")
                if isinstance(base_sensors, str):
                    logger.info(f"base_sensors is a string! First 100 chars: {base_sensors[:100]}")
                    import json
                    base_sensors = json.loads(base_sensors)
                    logger.info(f"Parsed base_sensors to list with {len(base_sensors)} items")

                if not base_sensors:
                    logger.warning(f"Feature config {feature_config_id} has no base_sensors")
                    continue

                # Query Feature Store for current snapshot of all required sensors
                logger.info(f"Querying Feature Store for {len(base_sensors)} sensors...")
                snapshot, as_of = await self.feature_store.get_current_snapshot(
                    company_id=company_id,
                    equipment_id=equipment_id,
                    sensor_names=base_sensors,
                    max_age_seconds=self.feature_max_age_seconds,
                )
                logger.info(f"Feature Store returned {len(snapshot)} sensors")

                if len(snapshot) < len(base_sensors):
                    logger.info(f"Incomplete sensor snapshot for equipment {equipment_id[:8]}...: {len(snapshot)}/{len(base_sensors)} fresh sensors available")
                    # Skip if we don't have enough fresh sensors
                    continue

                # Add equipment_id to snapshot for ML inference
                snapshot['equipment_id'] = equipment_id

                # Create sensor_data dict for ML evaluation. The event time is the snapshot's own
                # (its newest reading), not the wall clock, so the same snapshot evaluated again —
                # by a later pass or by another replica — maps to the same alert.
                event_time = timestamp or (as_of.isoformat() if as_of else None)
                sensor_data = {
                    'timestamp': event_time or datetime.now(timezone.utc).isoformat(),
                    'equipment_id': equipment_id,
                    **snapshot  # All sensor values
                }
                logger.info(f"Calling ML inference for rule {rule.get('rule_id')}")

                # Evaluate ML rule
                alert = await self._evaluate_ml(sensor_data, rule, company_id)
                if alert and await self._emit(alert):
                    triggered_alerts.append(alert)
                    logger.info(f"ML alert triggered for equipment {equipment_id[:8]}...")

            except Exception as e:
                logger.error(f"Failed to evaluate ML rule {rule.get('rule_id')}: {e}", exc_info=True)
                continue

        return triggered_alerts

    async def _get_feature_config_for_model(self, model_id: str, company_id: str) -> Optional[str]:
        """Get feature config ID for a model"""
        try:
            # Convert model_id to string in case it's a UUID object
            model_data = await self.alert_repo.get_model_by_id(str(model_id), str(company_id))
            if model_data:
                return model_data.get('feature_config_id')
        except Exception as e:
            logger.warning(f"Failed to get feature config for model {model_id}: {e}", exc_info=True)
        return None

    def _get_applicable_rules(
        self,
        sensor_id: str,
        equipment_id: str,
        rules: List[Dict],
        skip_ml_rules: bool = True
    ) -> List[Dict]:
        """
        Find rules that apply to this sensor (UUID matching only)

        Args:
            skip_ml_rules: If True, skip ML rules (they're evaluated separately at equipment level)
        """
        applicable = []

        # Convert to UUID for comparison
        try:
            sensor_uuid = uuid.UUID(sensor_id) if isinstance(sensor_id, str) else sensor_id
            equipment_uuid = uuid.UUID(equipment_id) if isinstance(equipment_id, str) and equipment_id else None
        except (ValueError, AttributeError):
            raise MessageValidationError(
                f"Invalid UUID format: sensor={sensor_id!r}, equipment={equipment_id!r}"
            ) from None

        for rule in rules:
            # Check if rule is enabled
            if not rule.get('enabled', True):
                continue

            # Skip ML rules during per-sensor evaluation (they need all sensors)
            if skip_ml_rules and rule.get('detection_type') == 'ml':
                continue
            
            # Match by exact sensor_id (UUID)
            rule_sensor_id = rule.get('sensor_id')
            if rule_sensor_id:
                try:
                    if uuid.UUID(str(rule_sensor_id)) == sensor_uuid:
                        applicable.append(rule)
                        continue
                except (ValueError, AttributeError):
                    pass
            
            # Match by equipment_id (UUID)
            rule_equipment_id = rule.get('equipment_id')
            if rule_equipment_id and equipment_uuid:
                try:
                    if uuid.UUID(str(rule_equipment_id)) == equipment_uuid:
                        applicable.append(rule)
                        continue
                except (ValueError, AttributeError):
                    pass
        
        return applicable
    
    async def _evaluate_rule(
        self,
        sensor_data: Dict[str, Any],
        rule: Dict,
        company_id: str
    ) -> Optional[Dict]:
        """Evaluate a single rule"""
        detection_type = rule.get('detection_type', 'threshold')

        if detection_type == 'threshold':
            return self._evaluate_threshold(sensor_data, rule, company_id)
        elif detection_type == 'ml':
            return await self._evaluate_ml(sensor_data, rule, company_id)
        elif detection_type == 'statistical':
            return self._evaluate_statistical(sensor_data, rule, company_id)
        else:
            logger.warning(f"Unknown detection type: {detection_type}")
            return None
    
    def _evaluate_threshold(
        self,
        sensor_data: Dict[str, Any],
        rule: Dict,
        company_id: str
    ) -> Optional[Dict]:
        """Evaluate threshold-based rule"""
        value = sensor_data['value']

        # Support both old (threshold_min/max) and new (condition/threshold) formats
        condition = rule.get('condition')
        threshold = rule.get('threshold')
        threshold_min = rule.get('threshold_min')
        threshold_max = rule.get('threshold_max')

        triggered = False
        condition_str = None
        threshold_value = None

        # New format: condition + threshold
        if condition and threshold is not None:
            if condition == 'greater_than' and value > threshold:
                triggered = True
                condition_str = 'greater_than'
                threshold_value = threshold
            elif condition == 'less_than' and value < threshold:
                triggered = True
                condition_str = 'less_than'
                threshold_value = threshold
            elif condition == 'equals' and value == threshold:
                triggered = True
                condition_str = 'equals'
                threshold_value = threshold
            elif condition == 'not_equals' and value != threshold:
                triggered = True
                condition_str = 'not_equals'
                threshold_value = threshold
        # Old format: threshold_min/max
        elif threshold_min is not None and value < threshold_min:
            triggered = True
            condition_str = 'below_min'
            threshold_value = threshold_min
        elif threshold_max is not None and value > threshold_max:
            triggered = True
            condition_str = 'above_max'
            threshold_value = threshold_max

        if not triggered:
            return None
        
        # Create alert
        timestamp = sensor_data.get('time') or sensor_data.get('timestamp')
        if isinstance(timestamp, str):
            timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        elif not timestamp:
            timestamp = datetime.now(timezone.utc)

        alert = {
            'company_id': company_id,
            'rule_id': str(rule['rule_id']),
            'sensor_id': str(sensor_data['sensor_id']),
            'equipment_id': str(sensor_data.get('equipment_id')) if sensor_data.get('equipment_id') else None,
            'site_id': sensor_data.get('site_id'),
            'triggered_at': timestamp,
            'detection_type': 'threshold',
            'actual_value': value,
            'threshold_value': threshold_value,
            'condition': condition_str,
            'severity': rule.get('severity', 'medium'),
            'message': f"Threshold alert: {rule['name']} - Value {value:.2f} is {condition_str.replace('_', ' ')}"
        }

        logger.info(f"Threshold alert triggered: {alert['message']}")
        return alert
    
    async def _evaluate_ml(
        self,
        sensor_data: Dict[str, Any],
        rule: Dict,
        company_id: str
    ) -> Optional[Dict]:
        """Evaluate ML-based rule by calling ML inference endpoint"""
        model_id = rule.get('model_id')

        if not model_id:
            logger.warning(f"ML rule {rule['rule_id']} has no model_id")
            return None

        try:
            # Get anomaly threshold from rule config
            anomaly_threshold = rule.get('anomaly_threshold', 0.85)

            # Call ML inference endpoint (authenticated service-to-service call).
            async with aiohttp.ClientSession() as session:
                inference_url = f"{self.ml_service_url}/api/inference/predict"

                payload = {
                    "model_id": str(model_id),
                    "sensor_data": sensor_data,
                    "threshold": anomaly_threshold,
                    "company_id": company_id
                }

                # Authenticate as an internal service so the ML endpoint trusts the
                # company_id in the body. Without the shared token the call fails closed.
                headers = {}
                if self.internal_service_token:
                    headers["X-Internal-Service-Token"] = self.internal_service_token

                async with session.post(inference_url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 404:
                        logger.warning(f"Model {model_id} not found for ML evaluation")
                        return None
                    elif response.status != 200:
                        error_text = await response.text()
                        logger.error(f"ML inference failed: {response.status} - {error_text}")
                        return None

                    result = await response.json()

            # Check if anomaly detected
            if not result.get('is_anomaly'):
                return None

            # Create alert
            timestamp = sensor_data.get('time') or sensor_data.get('timestamp')
            if isinstance(timestamp, str):
                timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            elif not timestamp:
                timestamp = datetime.now(timezone.utc)

            anomaly_score = result.get('prediction')

            # For equipment-level ML alerts, sensor_id might not be in sensor_data
            # Use rule's sensor_id if available, otherwise None (equipment-level alert)
            sensor_id = sensor_data.get('sensor_id') or rule.get('sensor_id')

            message = (f"ML anomaly detected: {rule['name']} - Anomaly score {anomaly_score:.4f} "
                       f"exceeds threshold {anomaly_threshold}")
            # ADR-0024 dec 5: a score computed with the stateful feature class neutralized is
            # lower-confidence, and the operator labelling this alert (ADR-0022) must see that.
            neutralized = list(result.get('neutralized_features') or [])
            degraded = bool(result.get('degraded')) or bool(neutralized)
            if degraded:
                detail = f": {', '.join(map(str, neutralized))}" if neutralized else ""
                message += (f" [DEGRADED: scored with stateful features neutralized{detail}; "
                            f"treat as lower-confidence]")

            alert = {
                'company_id': company_id,
                'rule_id': str(rule['rule_id']),
                'sensor_id': str(sensor_id) if sensor_id else None,
                'equipment_id': str(sensor_data.get('equipment_id')) if sensor_data.get('equipment_id') else None,
                'site_id': sensor_data.get('site_id'),
                'triggered_at': timestamp,
                'detection_type': 'ml',
                'actual_value': sensor_data.get('value'),
                'anomaly_score': anomaly_score,
                'model_id': str(model_id),
                'threshold_value': anomaly_threshold,
                'condition': 'ml_anomaly',
                'severity': rule.get('severity', 'medium'),
                'degraded': degraded,
                'neutralized_features': neutralized,
                'message': message,
            }

            logger.info(f"ML alert triggered: {alert['message']}")
            return alert

        except aiohttp.ClientError as e:
            logger.error(f"Failed to connect to ML service: {e}")
            return None
        except Exception as e:
            logger.error(f"ML evaluation failed: {e}", exc_info=True)
            return None

    def _evaluate_statistical(
        self,
        sensor_data: Dict[str, Any],
        rule: Dict,
        company_id: str
    ) -> Optional[Dict]:
        """Statistical rules are NOT evaluated per-event.

        The 'statistical' lane is model drift (ADR-0021), which is windowed and periodic —
        the wrong timescale for the per-reading path. It is evaluated by the scheduled
        evaluate_drift_rules() below, driven by the worker's periodic drift loop, and shares
        only the alert sink with this path. So a statistical rule reaching the per-event
        dispatch is a no-op here by design.
        """
        return None

    async def evaluate_drift_rules(
        self,
        company_id: str,
        window_minutes: int,
        default_share_threshold: float = 0.5,
    ) -> List[Dict]:
        """Evaluate all of a tenant's model-drift ('statistical') rules on a schedule.

        Windowed and periodic (ADR-0021 dec 5): for each enabled drift rule bound to a
        model, delegate the statistical compute to ml-service /api/drift/evaluate (mirroring
        how the real-time ml rules delegate to /api/inference), apply the rule's drift
        threshold, and fire a statistical alert into the shared alert sink.
        """
        rules = self.tenant_rules.get(company_id, [])
        drift_rules = [
            rule for rule in rules
            if rule.get('enabled')
            and rule.get('detection_type') == 'statistical'
            and rule.get('model_id')
        ]
        if not drift_rules:
            return []

        triggered_alerts: List[Dict] = []
        for rule in drift_rules:
            try:
                alert = await self._evaluate_drift(
                    rule, company_id, window_minutes, default_share_threshold
                )
                if alert and await self._emit(alert):
                    triggered_alerts.append(alert)
                    logger.info(f"Drift alert triggered: {alert['message']}")
            except Exception as e:
                logger.error(f"Failed to evaluate drift rule {rule.get('rule_id')}: {e}", exc_info=True)
                continue

        return triggered_alerts

    async def _evaluate_drift(
        self,
        rule: Dict,
        company_id: str,
        window_minutes: int,
        default_share_threshold: float,
    ) -> Optional[Dict]:
        """Score one drift rule via ml-service and build a statistical alert if over threshold."""
        model_id = rule.get('model_id')

        # The rule's drift threshold (share of drifted features). Falls back to the
        # service default when the rule does not pin one.
        threshold = rule.get('threshold')
        if threshold is None:
            threshold = default_share_threshold

        try:
            async with aiohttp.ClientSession() as session:
                drift_url = f"{self.ml_service_url}/api/drift/evaluate"
                payload = {
                    "model_id": str(model_id),
                    "company_id": company_id,
                    "window_minutes": window_minutes,
                    "drift_share_threshold": threshold,
                }
                headers = {}
                if self.internal_service_token:
                    headers["X-Internal-Service-Token"] = self.internal_service_token

                # Drift compute (windowed read + evidently) is heavier than per-event
                # inference; allow a longer budget than the 10s inference timeout.
                async with session.post(
                    drift_url, json=payload, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    if response.status == 404:
                        logger.warning(f"Model {model_id} not found for drift evaluation")
                        return None
                    if response.status != 200:
                        error_text = await response.text()
                        logger.error(f"Drift evaluation failed: {response.status} - {error_text}")
                        return None
                    result = await response.json()
        except aiohttp.ClientError as e:
            logger.error(f"Failed to connect to ML service for drift: {e}")
            return None

        status = result.get('status')
        if status != 'ok':
            # unavailable (no reference profile) / insufficient_data / error — nothing to fire.
            logger.info(f"Drift not evaluable for model {model_id}: {status} ({result.get('reason')})")
            return None

        data_drift = result.get('data_drift', {})
        share = data_drift.get('drift_share')
        prediction_drift = result.get('prediction_drift') or {}

        data_drifted = share is not None and share > threshold
        pred_drifted = bool(prediction_drift.get('drifted'))

        if not (data_drifted or pred_drifted):
            return None

        # Copy per ADR-0021: a drift alert means "distribution changed — investigate /
        # consider retraining", NOT "the model is wrong" (avoid alarm fatigue).
        share_txt = f"{share:.2f}" if share is not None else "n/a"
        message = (
            f"Model drift: {rule['name']} — input drift share {share_txt} vs threshold "
            f"{threshold:.2f}. Distribution has shifted from training; consider retraining "
            f"(this flags change, not a model-correctness failure)."
        )
        if pred_drifted:
            message += " Prediction-output drift also detected."

        alert = {
            'company_id': company_id,
            'rule_id': str(rule['rule_id']),
            'sensor_id': str(rule['sensor_id']) if rule.get('sensor_id') else None,
            'equipment_id': str(rule['equipment_id']) if rule.get('equipment_id') else None,
            'site_id': rule.get('site_id'),
            'triggered_at': datetime.now(timezone.utc),
            'detection_type': 'statistical',
            'actual_value': share,
            'anomaly_score': share,
            'model_id': str(model_id),
            'threshold_value': threshold,
            'condition': 'drift',
            'severity': rule.get('severity', 'medium'),
            'message': message,
        }
        return alert

    async def evaluate_retrain_recommendations(
        self,
        company_id: str,
        *,
        precision_floor: float,
        min_labels: int,
        min_drift_alerts: int,
        precision_window_days: int,
        drift_lookback_hours: int,
        cooldown_seconds: float,
        severity: str = 'high',
    ) -> List[Dict]:
        """Raise a retrain *recommendation* for models with sustained drift AND precision decay.

        The honest closed loop (ADR-0022 dec 4): this does NOT train anything — no training
        service exists. It fires a distinct high-severity 'statistical' alert (condition
        'retrain_recommended') telling an operator to retrain via the notebook flow, only when
        the operator-labelled precision has fallen below the floor AND drift has been sustained.
        Runs on the drift schedule, right after the drift pass, per tenant.
        """
        rules = self.tenant_rules.get(company_id, [])
        drift_rules = [
            rule for rule in rules
            if rule.get('enabled')
            and rule.get('detection_type') == 'statistical'
            and rule.get('model_id')
        ]
        if not drift_rules:
            return []

        triggered: List[Dict] = []
        seen_models = set()
        for rule in drift_rules:
            model_id = str(rule['model_id'])
            if model_id in seen_models:  # recommend once per model, not once per rule
                continue
            seen_models.add(model_id)
            try:
                signals = await self.alert_repo.get_retrain_signals(
                    model_id, company_id, precision_window_days, drift_lookback_hours,
                )
                precision = should_recommend_retrain(
                    signals['tp'], signals['fp'], signals['recent_drift_alerts'],
                    precision_floor=precision_floor, min_labels=min_labels,
                    min_drift_alerts=min_drift_alerts,
                )
                if precision is None:
                    continue
                alert = self._build_retrain_alert(rule, company_id, precision, signals, severity)
                if not await self._emit(alert, retrain=True, cooldown_seconds=cooldown_seconds):
                    continue
                triggered.append(alert)
                logger.info(f"Retrain recommendation: {alert['message']}")
            except Exception as e:
                logger.error(
                    f"Failed retrain recommendation for model {model_id}: {e}", exc_info=True,
                )
                continue

        return triggered

    def _build_retrain_alert(
        self, rule: Dict, company_id: str, precision: float, signals: Dict, severity: str,
    ) -> Dict:
        """Build the retrain-recommendation alert (a distinct 'statistical' condition)."""
        decided = signals['tp'] + signals['fp']
        message = (
            f"Retrain recommended: {rule['name']} — operator-labelled precision "
            f"{precision:.2f} over {decided} label(s), with {signals['recent_drift_alerts']} "
            f"recent drift alert(s). Sustained drift together with falling precision suggests "
            f"the model has decayed; retrain it (manual/notebook flow)."
        )
        return {
            'company_id': company_id,
            'rule_id': str(rule['rule_id']),
            'sensor_id': str(rule['sensor_id']) if rule.get('sensor_id') else None,
            'equipment_id': str(rule['equipment_id']) if rule.get('equipment_id') else None,
            'site_id': rule.get('site_id'),
            'triggered_at': datetime.now(timezone.utc),
            'detection_type': 'statistical',
            'actual_value': precision,
            'anomaly_score': precision,
            'model_id': str(rule['model_id']),
            'threshold_value': None,  # the precision floor is service config, not a rule threshold
            'condition': 'retrain_recommended',
            'severity': severity,
            'message': message,
        }
