import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import './AlertRules.css';

const API_BASE_URL = 'http://localhost:8001';

function AlertRules() {
  const [activeTab, setActiveTab] = useState('all');
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [editingRule, setEditingRule] = useState(null);
  const [showForm, setShowForm] = useState(false);
  const [user, setUser] = useState(null);
  const [connected, setConnected] = useState(false);
  useEffect(() => {
    // Get user from localStorage
    const userData = localStorage.getItem('user');
    if (userData) {
      setUser(JSON.parse(userData));
    }

    // Check API connection
    fetch('http://localhost:8000/health')
      .then(res => res.json())
      .then(() => setConnected(true))
      .catch(() => setConnected(false));

    fetchRules();
  }, []);

  const fetchRules = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(`${API_BASE_URL}/api/alert-rules?enabled_only=false`, {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });
      if (response.ok) {
        const data = await response.json();
        setRules(data);
      }
    } catch (error) {
      console.error('Error fetching alert rules:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleEdit = (rule) => {
    setEditingRule(rule);
    setShowForm(true);
  };

  const handleCloseForm = () => {
    setEditingRule(null);
    setShowForm(false);
  };

  const handleSaveRule = async (payload) => {
    try {
      const token = localStorage.getItem('access_token');
      let response;

      if (payload.rule_id) {
        response = await fetch(`${API_BASE_URL}/api/alert-rules/${payload.rule_id}`, {
          method: 'PUT',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(payload),
        });
      } else {
        response = await fetch(`${API_BASE_URL}/api/alert-rules`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`
          },
          body: JSON.stringify(payload),
        });
      }

      if (response.ok) {
        await fetchRules();
        handleCloseForm();
        alert(payload.rule_id ? 'Rule updated successfully!' : 'Rule created successfully!');
      } else {
        const error = await response.json();
        alert(`Failed to save rule: ${error.detail || 'Unknown error'}`);
      }
    } catch (error) {
      console.error('Error saving rule:', error);
      alert('Error saving rule');
    }
  };

  const handleDelete = async (ruleId) => {
    if (!window.confirm('Are you sure you want to delete this rule?')) {
      return;
    }

    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(`${API_BASE_URL}/api/alert-rules/${ruleId}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (response.ok) {
        await fetchRules();
        alert('Rule deleted successfully!');
      } else {
        alert('Failed to delete rule');
      }
    } catch (error) {
      console.error('Error deleting rule:', error);
      alert('Error deleting rule');
    }
  };

  const getFilteredRules = () => {
    if (activeTab === 'all') return rules;
    if (activeTab === 'default') return rules.filter(r => r.is_default);
    if (activeTab === 'overrides') return rules.filter(r => !r.is_default && r.sensor_id);
    if (activeTab === 'ml') return rules.filter(r => r.detection_type === 'ml_model');
    return rules;
  };

  const filteredRules = getFilteredRules();



  const DetectionBadge = ({ type }) => {
    const colors = {
      threshold: '#26a69a',
      ml_model: '#2962ff',
      hybrid: '#9c27b0',
      disabled: '#787b86'
    };
    return (
      <span style={{
        background: colors[type] || '#787b86',
        color: 'white',
        padding: '4px 8px',
        borderRadius: '4px',
        fontSize: '11px',
        fontWeight: '600',
        textTransform: 'uppercase'
      }}>
        {type.replace('_', ' ')}
      </span>
    );
  };

  const SeverityBadge = ({ severity }) => {
    const colors = {
      critical: '#ef5350',
      warning: '#ffa726',
      info: '#42a5f5'
    };
    return (
      <span style={{
        color: colors[severity] || '#787b86',
        fontWeight: '600',
        fontSize: '12px',
        textTransform: 'uppercase'
      }}>
        {severity}
      </span>
    );
  };

  return (
    <div className="App">
      <Header user={user} connected={connected} />

      <div className="alert-rules-page">
        <div className="page-header">
          <div>
            <h1>Alert Rules Management</h1>
            <p style={{ color: '#787b86', margin: '5px 0 0 0', fontSize: '14px' }}>
              Configure threshold and ML-based alert rules for sensor monitoring
            </p>
          </div>
          <button className="btn-primary" onClick={() => setShowForm(true)}>
            + New Rule
          </button>
        </div>

        <div className="tabs">
          <button
            className={`tab ${activeTab === 'all' ? 'active' : ''}`}
            onClick={() => setActiveTab('all')}
          >
            All Rules ({rules.length})
          </button>
          <button
            className={`tab ${activeTab === 'default' ? 'active' : ''}`}
            onClick={() => setActiveTab('default')}
          >
            Default Rules ({rules.filter(r => r.is_default).length})
          </button>
          <button
            className={`tab ${activeTab === 'overrides' ? 'active' : ''}`}
            onClick={() => setActiveTab('overrides')}
          >
            Sensor Overrides ({rules.filter(r => !r.is_default && r.sensor_id).length})
          </button>
          <button
            className={`tab ${activeTab === 'ml' ? 'active' : ''}`}
            onClick={() => setActiveTab('ml')}
          >
            ML Models ({rules.filter(r => r.detection_type === 'ml_model').length})
          </button>
        </div>

        <div className="tab-content">
          {loading ? (
            <p style={{ textAlign: 'center', color: '#787b86', padding: '40px' }}>
              Loading rules...
            </p>
          ) : filteredRules.length === 0 ? (
            <p style={{ textAlign: 'center', color: '#787b86', padding: '40px' }}>
              No rules found
            </p>
          ) : (
            <table className="rules-table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Target</th>
                  <th>Detection Type</th>
                  <th>Condition</th>
                  <th>Severity</th>
                  <th>Status</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filteredRules.map(rule => (
                  <tr key={rule.rule_id}>
                    <td>
                      <div style={{ fontWeight: '500' }}>{rule.name}</div>
                      {rule.is_default && (
                        <div style={{ fontSize: '11px', color: '#787b86', marginTop: '2px' }}>
                          Default Rule
                        </div>
                      )}
                    </td>
                    <td>
                      <div style={{ fontSize: '12px' }}>
                        {rule.sensor_id || rule.sensor_pattern || rule.equipment_id || 'All'}
                      </div>
                    </td>
                    <td>
                      <DetectionBadge type={rule.detection_type} />
                    </td>
                    <td>
                      {rule.detection_type === 'threshold' && rule.condition && (
                        <span style={{ fontSize: '12px' }}>
                          {rule.condition === 'between' || rule.condition === 'outside_range'
                            ? `${rule.threshold_min} - ${rule.threshold_max}`
                            : `${rule.condition} ${rule.threshold}`}
                        </span>
                      )}
                      {rule.detection_type === 'ml_model' && (
                        <span style={{ fontSize: '12px' }}>ML Score {rule.threshold || 0.85}</span>
                      )}
                    </td>
                    <td>
                      <SeverityBadge severity={rule.severity} />
                    </td>
                    <td>
                      <span style={{
                        color: rule.enabled ? '#26a69a' : '#787b86',
                        fontWeight: '500'
                      }}>
                        {rule.enabled ? 'Enabled' : 'Disabled'}
                      </span>
                    </td>
                    <td>
                      <button
                        className="btn-action"
                        onClick={() => handleEdit(rule)}
                      >
                        Edit
                      </button>
                      <button
                        className="btn-action"
                        style={{ marginLeft: '5px' }}
                        onClick={() => handleDelete(rule.rule_id)}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {showForm && (
          <EditRuleModal
            rule={editingRule}
            onClose={handleCloseForm}
            onSave={handleSaveRule}
          />
        )}
      </div>
    </div>
  );
}

// Edit Rule Modal Component
function EditRuleModal({ rule, onClose, onSave }) {
  const [formData, setFormData] = useState({
    rule_id: rule?.rule_id,
    company_id: rule?.company_id || 'acme_manufacturing',
    name: rule?.name || '',
    description: rule?.description || '',
    detection_type: rule?.detection_type || 'threshold',
    sensor_id: rule?.sensor_id || '',
    sensor_pattern: rule?.sensor_pattern || '',
    equipment_id: rule?.equipment_id || '',
    site_id: rule?.site_id || '',
    condition: rule?.condition || 'greater_than',
    threshold: rule?.threshold || '',
    threshold_min: rule?.threshold_min || '',
    threshold_max: rule?.threshold_max || '',
    severity: rule?.severity || 'warning',
    priority: rule?.priority || 0,
    enabled: rule?.enabled !== false,
    is_default: rule?.is_default || false,
  });

  const [ruleLevel, setRuleLevel] = useState(
    rule?.equipment_id ? 'equipment' : rule?.sensor_id ? 'sensor' : 'pattern'
  );
  const [sensors, setSensors] = useState([]);
  const [equipment, setEquipment] = useState([]);
  const [loadingData, setLoadingData] = useState(false);

  useEffect(() => {
    fetchSensorsAndEquipment();
  }, []);

  const fetchSensorsAndEquipment = async () => {
    setLoadingData(true);
    try {
      const token = localStorage.getItem('access_token');

      // Fetch sensors from cache
      const sensorsResponse = await fetch('http://localhost:8000/api/cache/sensors', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (sensorsResponse.ok) {
        const sensorsData = await sensorsResponse.json();
        const sensorsList = Object.entries(sensorsData.sensors || {}).map(([id, data]) => ({
          sensor_id: id,
          sensor_name: data.sensor_name || id,
          equipment_name: data.equipment_name
        }));
        setSensors(sensorsList);
      }

      // Fetch equipment
      const equipmentResponse = await fetch('http://localhost:8000/api/equipment', {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (equipmentResponse.ok) {
        const equipmentData = await equipmentResponse.json();
        setEquipment(equipmentData);
      }
    } catch (error) {
      console.error('Error fetching sensors/equipment:', error);
    } finally {
      setLoadingData(false);
    }
  };

  const handleSubmit = (e) => {
    e.preventDefault();

    const payload = {
      name: formData.name,
      description: formData.description || '',
      detection_type: formData.detection_type,
      severity: formData.severity,
      enabled: formData.enabled,
      company_id: formData.company_id,
    };

    // Handle target based on rule level
    if (ruleLevel === 'sensor' && formData.sensor_id) {
      payload.sensor_id = formData.sensor_id;
    } else if (ruleLevel === 'equipment' && formData.equipment_id) {
      payload.equipment_id = formData.equipment_id;
    } else if (ruleLevel === 'pattern' && formData.sensor_pattern) {
      payload.sensor_pattern = formData.sensor_pattern;
    } else if (!rule) {
      alert('Please select a target for the alert rule');
      return;
    }

    if (formData.detection_type === 'threshold') {
      payload.condition = formData.condition;
      if (formData.condition === 'outside_range' || formData.condition === 'between') {
        payload.threshold_min = parseFloat(formData.threshold_min);
        payload.threshold_max = parseFloat(formData.threshold_max);
      } else {
        payload.threshold = parseFloat(formData.threshold);
      }
    }

    if (rule) {
      payload.rule_id = formData.rule_id;
    }

    onSave(payload);
  };

  const handleRuleLevelChange = (level) => {
    setRuleLevel(level);
    // Clear all target fields when switching levels
    setFormData({
      ...formData,
      sensor_id: '',
      equipment_id: '',
      sensor_pattern: ''
    });
  };

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <h2>{rule ? 'Edit Rule' : 'Create New Rule'}</h2>

        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Rule Name</label>
            <input
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              required
            />
          </div>

          <div className="form-group">
            <label>Description</label>
            <input
              type="text"
              value={formData.description}
              onChange={(e) => setFormData({ ...formData, description: e.target.value })}
            />
          </div>

          <div className="form-group">
            <label>Rule Target Level</label>
            <select
              value={ruleLevel}
              onChange={(e) => handleRuleLevelChange(e.target.value)}
            >
              <option value="pattern">Pattern Matching (e.g., temp_*)</option>
              <option value="sensor">Specific Sensor</option>
              <option value="equipment">Equipment Level</option>
            </select>
          </div>

          {ruleLevel === 'pattern' && (
            <div className="form-group">
              <label>Sensor Pattern</label>
              <input
                type="text"
                value={formData.sensor_pattern}
                onChange={(e) => setFormData({ ...formData, sensor_pattern: e.target.value })}
                placeholder="temp_*, press_*, *_motor_*"
                required
              />
              <small style={{ color: '#787b86', fontSize: '11px', marginTop: '4px', display: 'block' }}>
                Use * as wildcard. Examples: temp_* (all temperature sensors), *_A1 (all sensors ending in _A1)
              </small>
            </div>
          )}

          {ruleLevel === 'sensor' && (
            <div className="form-group">
              <label>Select Sensor</label>
              {loadingData ? (
                <p style={{ color: '#787b86', fontSize: '12px' }}>Loading sensors...</p>
              ) : (
                <select
                  value={formData.sensor_id}
                  onChange={(e) => setFormData({ ...formData, sensor_id: e.target.value })}
                  required
                >
                  <option value="">-- Select a sensor --</option>
                  {sensors.map(sensor => (
                    <option key={sensor.sensor_id} value={sensor.sensor_id}>
                      {sensor.sensor_name} ({sensor.sensor_id})
                      {sensor.equipment_name && ` - ${sensor.equipment_name}`}
                    </option>
                  ))}
                </select>
              )}
              {sensors.length === 0 && !loadingData && (
                <small style={{ color: '#ffa726', fontSize: '11px', marginTop: '4px', display: 'block' }}>
                  No sensors found. Make sure sensors are streaming data.
                </small>
              )}
            </div>
          )}

          {ruleLevel === 'equipment' && (
            <div className="form-group">
              <label>Select Equipment</label>
              {loadingData ? (
                <p style={{ color: '#787b86', fontSize: '12px' }}>Loading equipment...</p>
              ) : (
                <select
                  value={formData.equipment_id}
                  onChange={(e) => setFormData({ ...formData, equipment_id: e.target.value })}
                  required
                >
                  <option value="">-- Select equipment --</option>
                  {equipment.map(eq => (
                    <option key={eq.equipment_id} value={eq.equipment_id}>
                      {eq.name} ({eq.equipment_id}) - {eq.equipment_type}
                    </option>
                  ))}
                </select>
              )}
              {equipment.length === 0 && !loadingData && (
                <small style={{ color: '#ffa726', fontSize: '11px', marginTop: '4px', display: 'block' }}>
                  No equipment found. Create equipment first.
                </small>
              )}
            </div>
          )}

          <div className="form-group">
            <label>Detection Type</label>
            <select
              value={formData.detection_type}
              onChange={(e) => setFormData({ ...formData, detection_type: e.target.value })}
            >
              <option value="threshold">Threshold</option>
              <option value="ml_model">ML Model</option>
              <option value="hybrid">Hybrid</option>
              <option value="disabled">Disabled</option>
            </select>
          </div>

          {formData.detection_type === 'threshold' && (
            <>
              <div className="form-group">
                <label>Condition</label>
                <select
                  value={formData.condition}
                  onChange={(e) => setFormData({ ...formData, condition: e.target.value })}
                >
                  <option value="greater_than">Greater Than</option>
                  <option value="less_than">Less Than</option>
                  <option value="equals">Equals</option>
                  <option value="between">Between</option>
                  <option value="outside_range">Outside Range</option>
                </select>
              </div>

              {(formData.condition === 'between' || formData.condition === 'outside_range') ? (
                <>
                  <div className="form-group">
                    <label>Minimum Value</label>
                    <input
                      type="number"
                      step="0.01"
                      value={formData.threshold_min}
                      onChange={(e) => setFormData({ ...formData, threshold_min: e.target.value })}
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label>Maximum Value</label>
                    <input
                      type="number"
                      step="0.01"
                      value={formData.threshold_max}
                      onChange={(e) => setFormData({ ...formData, threshold_max: e.target.value })}
                      required
                    />
                  </div>
                </>
              ) : (
                <div className="form-group">
                  <label>Threshold Value</label>
                  <input
                    type="number"
                    step="0.01"
                    value={formData.threshold}
                    onChange={(e) => setFormData({ ...formData, threshold: e.target.value })}
                    required
                  />
                </div>
              )}
            </>
          )}

          <div className="form-group">
            <label>Severity</label>
            <select
              value={formData.severity}
              onChange={(e) => setFormData({ ...formData, severity: e.target.value })}
            >
              <option value="info">Info</option>
              <option value="warning">Warning</option>
              <option value="critical">Critical</option>
            </select>
          </div>

          <div className="form-group">
            <label style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <input
                type="checkbox"
                checked={formData.enabled}
                onChange={(e) => setFormData({ ...formData, enabled: e.target.checked })}
              />
              Enabled
            </label>
          </div>

          <div className="form-actions">
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="btn-primary">
              Save Rule
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AlertRules;
