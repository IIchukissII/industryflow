"""
Rules Engine - Evaluates sensor data against alert rules
Schema-per-tenant architecture - UUID-based matching only
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid

logger = logging.getLogger(__name__)


class RulesEngine:
    """Evaluates sensor data against alert rules with schema-per-tenant"""
    
    def __init__(self, tenant_rules: Dict[str, List[Dict]], alert_repo):
        """
        Initialize with rules organized by tenant
        tenant_rules: {company_id: [rules]}
        """
        self.tenant_rules = tenant_rules
        self.alert_repo = alert_repo
        self.ml_detectors = {}  # model_id -> detector (lazy loaded)
        
        # Log loaded rules
        total = sum(len(rules) for rules in tenant_rules.values())
        logger.info(f"Rules engine initialized with {total} rules from {len(tenant_rules)} tenants")
    
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
        
        if not sensor_id or value is None:
            logger.warning(f"Invalid sensor data: {sensor_data}")
            return []
        
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
        threshold_min = rule.get('threshold_min')
        threshold_max = rule.get('threshold_max')
        
        triggered = False
        condition = None
        threshold_value = None
        
        if threshold_min is not None and value < threshold_min:
            triggered = True
            condition = 'below_min'
            threshold_value = threshold_min
        elif threshold_max is not None and value > threshold_max:
            triggered = True
            condition = 'above_max'
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
            'condition': condition,
            'severity': rule.get('severity', 'medium'),
            'message': f"Threshold alert: {rule['name']} - Value {value:.2f} is {condition.replace('_', ' ')}"
        }
        
        logger.info(f"Threshold alert triggered: {alert['message']}")
        return alert
    
    async def _evaluate_ml(
        self, 
        sensor_data: Dict[str, Any], 
        rule: Dict,
        company_id: str
    ) -> Optional[Dict]:
        """Evaluate ML-based rule (placeholder for now)"""
        model_id = rule.get('model_id')
        
        if not model_id:
            logger.warning(f"ML rule {rule['rule_id']} has no model_id")
            return None
        
        # TODO: Implement ML evaluation in later phase
        logger.debug(f"ML evaluation not yet implemented for model {model_id}")
        return None
