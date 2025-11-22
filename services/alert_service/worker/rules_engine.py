"""
Rules Engine - Evaluates sensor data against alert rules
Schema-per-tenant architecture - UUID-based matching only
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
import uuid
import aiohttp
import os

logger = logging.getLogger(__name__)

ML_SERVICE_URL = os.getenv('ML_SERVICE_URL', 'http://ml-service:8002')


class RulesEngine:
    """Evaluates sensor data against alert rules with schema-per-tenant"""

    def __init__(self, tenant_rules: Dict[str, List[Dict]], alert_repo, feature_store=None):
        """
        Initialize with rules organized by tenant
        tenant_rules: {company_id: [rules]}
        """
        self.tenant_rules = tenant_rules
        self.alert_repo = alert_repo
        self.feature_store = feature_store
        self.ml_detectors = {}  # model_id -> detector (lazy loaded)

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
    
    async def evaluate(self, sensor_data: Dict[str, Any], company_id: str) -> List[Dict]:
        """
        Evaluate sensor data against rules for the tenant
        Returns list of triggered alerts
        """
        sensor_id = sensor_data.get('sensor_id')
        equipment_id = sensor_data.get('equipment_id')
        value = sensor_data.get('value')
        timestamp = sensor_data.get('timestamp')

        if not sensor_id or value is None:
            logger.warning(f"Invalid sensor data: {sensor_data}")
            return []

        # Store reading in Feature Store for ML inference
        if self.feature_store and equipment_id:
            try:
                # Use sensor_id as sensor_name for now
                # TODO: Add sensor name mapping later
                await self.feature_store.store_reading(
                    equipment_id=equipment_id,
                    sensor_name=sensor_id,
                    timestamp=timestamp or datetime.now().isoformat(),
                    value=float(value)
                )
            except Exception as e:
                logger.error(f"Failed to store reading in Feature Store: {e}")
                # Continue with rule evaluation even if Feature Store fails

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
            if alert:
                triggered_alerts.append(alert)
                # Save alert to database
                await self.alert_repo.save_alert(alert)
        
        return triggered_alerts
    
    def _get_applicable_rules(
        self, 
        sensor_id: str, 
        equipment_id: str, 
        rules: List[Dict]
    ) -> List[Dict]:
        """Find rules that apply to this sensor (UUID matching only)"""
        applicable = []
        
        # Convert to UUID for comparison
        try:
            sensor_uuid = uuid.UUID(sensor_id) if isinstance(sensor_id, str) else sensor_id
            equipment_uuid = uuid.UUID(equipment_id) if isinstance(equipment_id, str) and equipment_id else None
        except (ValueError, AttributeError):
            logger.warning(f"Invalid UUID format: sensor={sensor_id}, equipment={equipment_id}")
            return []
        
        for rule in rules:
            # Check if rule is enabled
            if not rule.get('enabled', True):
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
            timestamp = datetime.utcnow()

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

            # Call ML inference endpoint
            async with aiohttp.ClientSession() as session:
                inference_url = f"{ML_SERVICE_URL}/api/inference/predict"

                payload = {
                    "model_id": str(model_id),
                    "sensor_data": sensor_data,
                    "threshold": anomaly_threshold
                }

                # Note: ML service inference endpoint is internal, no auth required
                # If auth is needed, add headers with service token
                async with session.post(inference_url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
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
                timestamp = datetime.utcnow()

            anomaly_score = result.get('prediction')

            alert = {
                'company_id': company_id,
                'rule_id': str(rule['rule_id']),
                'sensor_id': str(sensor_data['sensor_id']),
                'equipment_id': str(sensor_data.get('equipment_id')) if sensor_data.get('equipment_id') else None,
                'site_id': sensor_data.get('site_id'),
                'triggered_at': timestamp,
                'detection_type': 'ml',
                'actual_value': sensor_data.get('value'),
                'predicted_value': anomaly_score,
                'threshold_value': anomaly_threshold,
                'condition': 'ml_anomaly',
                'severity': rule.get('severity', 'medium'),
                'message': f"ML anomaly detected: {rule['name']} - Anomaly score {anomaly_score:.4f} exceeds threshold {anomaly_threshold}"
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
        """Evaluate statistical-based rule (placeholder for now)"""
        # TODO: Implement statistical evaluation (z-score, moving average, etc.)
        logger.debug(f"Statistical evaluation not yet implemented for rule {rule['rule_id']}")
        return None
