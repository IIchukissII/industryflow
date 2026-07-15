// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, useCallback } from 'react';
import Icon from './Icon';
import './EquipmentConfigModal.css';
import { createEquipment, updateEquipment, getEquipmentSensors, removeSensorFromEquipment } from '../services/equipmentApi';
import api from '../services/api';

function EquipmentConfigModal({ isOpen, onClose, onSuccess, editEquipment = null }) {
  const [step, setStep] = useState(1); // 1: Basic Info, 2: Configure Sensors, 3: Review & Submit
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  
  // Equipment form state. Initialised from `editEquipment` when editing, else the create defaults.
  // The modal is keyed by equipment id at the render site, so a different target remounts it and
  // this initialiser runs fresh — no effect mirrors the prop into state.
  const [formData, setFormData] = useState(() => editEquipment ? {
    equipment_id: editEquipment.equipment_id,
    equipment_type: editEquipment.equipment_type,
    name: editEquipment.name,
    description: editEquipment.description || '',
    site_id: editEquipment.site_id || '',
    location: editEquipment.location || '',
    sensor_count: editEquipment.sensor_count,
    expected_sensors: editEquipment.expected_sensors || [],
    batch_timeout_seconds: editEquipment.batch_timeout_seconds,
    require_complete_batch: editEquipment.require_complete_batch,
    min_sensors_for_partial: editEquipment.min_sensors_for_partial
  } : {
    equipment_id: '',
    equipment_type: 'pump',
    name: '',
    description: '',
    site_id: '',
    location: '',
    sensor_count: 1,
    expected_sensors: [],
    batch_timeout_seconds: 5,
    require_complete_batch: true,
    min_sensors_for_partial: null
  });

  // Array of sensors to be added
  const [sensors, setSensors] = useState([]);
  
  // Current sensor being configured
  const [currentSensor, setCurrentSensor] = useState({
    sensor_id: '',
    sensor_type: 'temperature',
    unit: 'celsius',
    description: '',
    position: 0,
    is_critical: false,
    is_required_for_ml: true,
    normal_min: '',
    normal_max: ''
  });

  const [createdEquipmentId, setCreatedEquipmentId] = useState(editEquipment?.equipment_id ?? null);
  const [isEditMode, setIsEditMode] = useState(!!editEquipment);

  const loadExistingSensors = useCallback(async (equipmentId) => {
    try {
      const data = await getEquipmentSensors(equipmentId);
      setSensors(data);
      setCurrentSensor(prev => ({ ...prev, position: data.length }));
      if (data.length > 0) {
        setStep(3); // Go to review if sensors already exist
      } else {
        setStep(2); // Go to sensor config if no sensors
      }
    } catch (err) {
      console.error('Error loading sensors:', err);
      setStep(2);
    }
  }, []);

  // Editing: pull the equipment's existing sensors and jump to the right step. The form fields are
  // initialised from the prop above, so this effect only performs the async fetch — invoked from an
  // inner async function so the effect body itself starts no synchronous setState.
  useEffect(() => {
    if (editEquipment) {
      (async () => { await loadExistingSensors(editEquipment.equipment_id); })();
    }
  }, [editEquipment, loadExistingSensors]);

  const handleInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setFormData(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
  };

  const handleSensorInputChange = (e) => {
    const { name, value, type, checked } = e.target;
    setCurrentSensor(prev => ({
      ...prev,
      [name]: type === 'checkbox' ? checked : value
    }));
  };

  const handleStep1Submit = async (e) => {
    e.preventDefault();
    setLoading(true);
    setError(null);

    try {
      const sensorCount = parseInt(formData.sensor_count);
      if (sensorCount < 1) {
        throw new Error('Sensor count must be at least 1');
      }

      const equipmentData = {
        ...formData,
        sensor_count: sensorCount,
        batch_timeout_seconds: parseInt(formData.batch_timeout_seconds),
        min_sensors_for_partial: formData.min_sensors_for_partial 
          ? parseInt(formData.min_sensors_for_partial) 
          : null
      };

      if (isEditMode) {
        await updateEquipment(createdEquipmentId, equipmentData);
      } else {
        const result = await createEquipment(equipmentData);
        setCreatedEquipmentId(result.equipment_id);
      }

      // Initialize sensors array based on sensor_count
      const initialSensors = Array.from({ length: sensorCount }, (_, i) => ({
        sensor_id: '',
        sensor_type: 'temperature',
        unit: 'celsius',
        description: '',
        position: i,
        is_critical: false,
        is_required_for_ml: true,
        normal_min: null,
        normal_max: null
      }));
      
      setSensors(initialSensors);
      setCurrentSensor({ ...currentSensor, position: 0 });
      setStep(2);
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleAddSensorToList = (e) => {
    e.preventDefault();
    setError(null);

    // Validate sensor_id is not empty
    if (!currentSensor.sensor_id.trim()) {
      setError('Sensor ID is required');
      return;
    }

    // Check for duplicate sensor_id
    if (sensors.some(s => s.sensor_id === currentSensor.sensor_id)) {
      setError('Sensor ID already exists');
      return;
    }

    // Update the sensor in the array
    const updatedSensors = [...sensors];
    updatedSensors[currentSensor.position] = {
      ...currentSensor,
      normal_min: currentSensor.normal_min ? parseFloat(currentSensor.normal_min) : null,
      normal_max: currentSensor.normal_max ? parseFloat(currentSensor.normal_max) : null
    };
    setSensors(updatedSensors);

    // Move to next sensor or review step
    const nextPosition = currentSensor.position + 1;
    if (nextPosition < formData.sensor_count) {
      setCurrentSensor({
        sensor_id: '',
        sensor_type: 'temperature',
        unit: 'celsius',
        description: '',
        position: nextPosition,
        is_critical: false,
        is_required_for_ml: true,
        normal_min: '',
        normal_max: ''
      });
    } else {
      setStep(3); // Move to review
    }
  };

  const handleEditSensor = (position) => {
    const sensor = sensors[position];
    setCurrentSensor({
      ...sensor,
      normal_min: sensor.normal_min !== null ? sensor.normal_min : '',
      normal_max: sensor.normal_max !== null ? sensor.normal_max : ''
    });
    setStep(2);
  };

  const handleRemoveSensorFromList = (position) => {
    // For edit mode with existing sensors
    if (isEditMode && sensors[position].created_at) {
      if (!window.confirm('Remove this sensor from database?')) return;
      removeSensorFromEquipment(createdEquipmentId, sensors[position].sensor_id)
        .then(() => loadExistingSensors(createdEquipmentId))
        .catch(err => setError(err.response?.data?.detail || err.message));
    } else {
      // Just remove from local array for new equipment
      const updatedSensors = [...sensors];
      updatedSensors[position] = {
        sensor_id: '',
        sensor_type: 'temperature',
        unit: 'celsius',
        description: '',
        position: position,
        is_critical: false,
        is_required_for_ml: true,
        normal_min: null,
        normal_max: null
      };
      setSensors(updatedSensors);
    }
  };

  const handleFinalSubmit = async () => {
    setLoading(true);
    setError(null);

    try {
      // Validate all sensors have IDs
      const incompleteSensors = sensors.filter(s => !s.sensor_id);
      if (incompleteSensors.length > 0) {
        throw new Error(`Please configure all ${formData.sensor_count} sensors`);
      }

      // Submit bulk sensors
      await api.post(`/api/equipment/${createdEquipmentId}/sensors/bulk`, {
        sensors: sensors
      });

      onSuccess();
      handleClose();
    } catch (err) {
      setError(err.response?.data?.detail || err.message);
    } finally {
      setLoading(false);
    }
  };

  const handleClose = () => {
    setStep(1);
    setFormData({
      equipment_id: '',
      equipment_type: 'pump',
      name: '',
      description: '',
      site_id: '',
      location: '',
      sensor_count: 1,
      expected_sensors: [],
      batch_timeout_seconds: 5,
      require_complete_batch: true,
      min_sensors_for_partial: null
    });
    setCurrentSensor({
      sensor_id: '',
      sensor_type: 'temperature',
      unit: 'celsius',
      description: '',
      position: 0,
      is_critical: false,
      is_required_for_ml: true,
      normal_min: '',
      normal_max: ''
    });
    setSensors([]);
    setCreatedEquipmentId(null);
    setIsEditMode(false);
    setError(null);
    onClose();
  };

  if (!isOpen) return null;

  const configuredCount = sensors.filter(s => s.sensor_id && s.sensor_id.trim() !== "").length;
  const isAllConfigured = configuredCount === parseInt(formData.sensor_count);
  console.log("Validation:", { configuredCount, sensorCount: formData.sensor_count, isAllConfigured, sensorsWithIds: sensors.filter(s => s.sensor_id).length });

  return (
    <div className="modal-overlay" onClick={handleClose}>
      <div className="modal-content equipment-config-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{isEditMode ? 'Edit Equipment' : 'Create New Equipment'}</h2>
          <button className="modal-close" onClick={handleClose}>×</button>
        </div>

        {error && <div className="error-message">{error}</div>}

        <div className="modal-body">
          {/* Step Indicator */}
          <div className="step-indicator">
            <div className={`step ${step === 1 ? 'active' : step > 1 ? 'completed' : ''}`}>
              <span className="step-number">1</span>
              <span className="step-label">Basic Info</span>
            </div>
            <div className="step-line"></div>
            <div className={`step ${step === 2 ? 'active' : step > 2 ? 'completed' : ''}`}>
              <span className="step-number">2</span>
              <span className="step-label">Configure Sensors</span>
            </div>
            <div className="step-line"></div>
            <div className={`step ${step === 3 ? 'active' : ''}`}>
              <span className="step-number">3</span>
              <span className="step-label">Review & Submit</span>
            </div>
          </div>

          {/* Step 1: Basic Equipment Info */}
          {step === 1 && (
            <form onSubmit={handleStep1Submit} className="equipment-form">
              <div className="form-row">
                <div className="form-group">
                  <label>Equipment ID *</label>
                  <input
                    type="text"
                    name="equipment_id"
                    value={formData.equipment_id}
                    onChange={handleInputChange}
                    placeholder="e.g., pump_001"
                    required
                    disabled={isEditMode}
                  />
                </div>
                <div className="form-group">
                  <label>Equipment Type *</label>
                  <select
                    name="equipment_type"
                    value={formData.equipment_type}
                    onChange={handleInputChange}
                    required
                  >
                    <option value="pump">Pump</option>
                    <option value="motor">Motor</option>
                    <option value="compressor">Compressor</option>
                    <option value="turbine">Turbine</option>
                    <option value="hvac">HVAC</option>
                    <option value="conveyor">Conveyor</option>
                    <option value="other">Other</option>
                  </select>
                </div>
              </div>

              <div className="form-group">
                <label>Name *</label>
                <input
                  type="text"
                  name="name"
                  value={formData.name}
                  onChange={handleInputChange}
                  placeholder="e.g., Main Circulation Pump"
                  required
                />
              </div>

              <div className="form-group">
                <label>Description</label>
                <textarea
                  name="description"
                  value={formData.description}
                  onChange={handleInputChange}
                  placeholder="Optional description..."
                  rows="3"
                />
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label>Site ID</label>
                  <input
                    type="text"
                    name="site_id"
                    value={formData.site_id}
                    onChange={handleInputChange}
                    placeholder="e.g., SITE-A"
                  />
                </div>
                <div className="form-group">
                  <label>Location</label>
                  <input
                    type="text"
                    name="location"
                    value={formData.location}
                    onChange={handleInputChange}
                    placeholder="e.g., Building 3, Floor 2"
                  />
                </div>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label>Number of Sensors *</label>
                  <input
                    type="number"
                    name="sensor_count"
                    value={formData.sensor_count}
                    onChange={handleInputChange}
                    min="1"
                    max="100"
                    required
                  />
                  <small>Total sensors on this equipment (1-100)</small>
                </div>
                <div className="form-group">
                  <label>Batch Timeout (seconds) *</label>
                  <input
                    type="number"
                    name="batch_timeout_seconds"
                    value={formData.batch_timeout_seconds}
                    onChange={handleInputChange}
                    min="1"
                    required
                  />
                  <small>Wait time for complete batch</small>
                </div>
              </div>

              <div className="form-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    name="require_complete_batch"
                    checked={formData.require_complete_batch}
                    onChange={handleInputChange}
                  />
                  <span>Require Complete Batch for ML Evaluation</span>
                </label>
              </div>

              {!formData.require_complete_batch && (
                <div className="form-group">
                  <label>Minimum Sensors for Partial Batch</label>
                  <input
                    type="number"
                    name="min_sensors_for_partial"
                    value={formData.min_sensors_for_partial || ''}
                    onChange={handleInputChange}
                    min="1"
                    max={formData.sensor_count}
                    placeholder="e.g., 3"
                  />
                  <small>Minimum sensors needed if partial batches are allowed</small>
                </div>
              )}

              <div className="form-actions">
                <button type="button" onClick={handleClose} className="btn-secondary">
                  Cancel
                </button>
                <button type="submit" disabled={loading} className="btn-primary">
                  {loading ? 'Saving...' : 'Next: Configure Sensors'}
                </button>
              </div>
            </form>
          )}

          {/* Step 2: Configure Sensors */}
          {step === 2 && (
            <div className="sensor-config-step">
              <div className="sensor-progress">
                <h3>Configuring Sensor {currentSensor.position + 1} of {formData.sensor_count}</h3>
                <p>Equipment: <strong>{formData.name}</strong></p>
                <p>Configured: <strong>{configuredCount}</strong> / <strong>{formData.sensor_count}</strong></p>
              </div>

              <form onSubmit={handleAddSensorToList} className="sensor-form">
                <h4>Sensor Configuration</h4>
                
                <div className="form-row">
                  <div className="form-group">
                    <label>Sensor ID *</label>
                    <input
                      type="text"
                      name="sensor_id"
                      value={currentSensor.sensor_id}
                      onChange={handleSensorInputChange}
                      placeholder="e.g., temp_sensor_01"
                      required
                    />
                  </div>
                  <div className="form-group">
                    <label>Position</label>
                    <input
                      type="number"
                      value={currentSensor.position}
                      disabled
                    />
                    <small>Auto-assigned (0-based index)</small>
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>Sensor Type *</label>
                    <select
                      name="sensor_type"
                      value={currentSensor.sensor_type}
                      onChange={handleSensorInputChange}
                      required
                    >
                      <option value="temperature">Temperature</option>
                      <option value="pressure">Pressure</option>
                      <option value="vibration">Vibration</option>
                      <option value="flow">Flow</option>
                      <option value="power">Power</option>
                      <option value="speed">Speed</option>
                      <option value="voltage">Voltage</option>
                      <option value="current">Current</option>
                      <option value="other">Other</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label>Unit</label>
                    <input
                      type="text"
                      name="unit"
                      value={currentSensor.unit}
                      onChange={handleSensorInputChange}
                      placeholder="e.g., celsius, bar, m/s"
                    />
                  </div>
                </div>

                <div className="form-group">
                  <label>Description</label>
                  <input
                    type="text"
                    name="description"
                    value={currentSensor.description}
                    onChange={handleSensorInputChange}
                    placeholder="Optional description..."
                  />
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label>Normal Min</label>
                    <input
                      type="number"
                      step="0.01"
                      name="normal_min"
                      value={currentSensor.normal_min}
                      onChange={handleSensorInputChange}
                      placeholder="e.g., 20.0"
                    />
                  </div>
                  <div className="form-group">
                    <label>Normal Max</label>
                    <input
                      type="number"
                      step="0.01"
                      name="normal_max"
                      value={currentSensor.normal_max}
                      onChange={handleSensorInputChange}
                      placeholder="e.g., 80.0"
                    />
                  </div>
                </div>

                <div className="form-row">
                  <div className="form-group">
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        name="is_critical"
                        checked={currentSensor.is_critical}
                        onChange={handleSensorInputChange}
                      />
                      <span>Critical Sensor</span>
                    </label>
                  </div>
                  <div className="form-group">
                    <label className="checkbox-label">
                      <input
                        type="checkbox"
                        name="is_required_for_ml"
                        checked={currentSensor.is_required_for_ml}
                        onChange={handleSensorInputChange}
                      />
                      <span>Required for ML</span>
                    </label>
                  </div>
                </div>

                <div className="form-actions">
                  <button type="button" onClick={() => setStep(1)} className="btn-secondary">
                    Back
                  </button>
                  {configuredCount > 0 && (
                    <button type="button" onClick={() => setStep(3)} className="btn-secondary">
                      Skip to Review
                    </button>
                  )}
                  <button type="submit" className="btn-primary">
                    {currentSensor.position < formData.sensor_count - 1 ? 'Next Sensor' : 'Review Configuration'}
                  </button>
                </div>
              </form>
            </div>
          )}

          {/* Step 3: Review & Submit */}
          {step === 3 && (
            <div className="sensor-config-step">
              <div className="sensor-progress">
                <h3>Review Configuration</h3>
                <p>Equipment: <strong>{formData.name}</strong></p>
                <p>Configured: <strong>{configuredCount}</strong> / <strong>{formData.sensor_count}</strong></p>
                {isAllConfigured && (
                  <div className="success-message" style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}><Icon name="check" size={15} /> All sensors configured!</div>
                )}
                {!isAllConfigured && (
                  <div className="error-message">
                    Please configure all {formData.sensor_count} sensors before submitting
                  </div>
                )}
              </div>

              <div className="sensor-list-container">
                <h4>Configured Sensors</h4>
                <div className="sensor-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Pos</th>
                        <th>Sensor ID</th>
                        <th>Type</th>
                        <th>Unit</th>
                        <th>Critical</th>
                        <th>Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {sensors.map((sensor, idx) => (
                        <tr key={idx} className={!sensor.sensor_id ? 'incomplete-row' : ''}>
                          <td>{sensor.position}</td>
                          <td>{sensor.sensor_id || <em className="text-muted">Not configured</em>}</td>
                          <td>{sensor.sensor_type}</td>
                          <td>{sensor.unit || '-'}</td>
                          <td>{sensor.is_critical ? <Icon name="check" size={14} color="var(--live)" /> : '–'}</td>
                          <td>
                            <button
                              type="button"
                              className="btn-edit-small"
                              onClick={() => handleEditSensor(idx)}
                            >
                              {sensor.sensor_id ? 'Edit' : 'Configure'}
                            </button>
                            {sensor.sensor_id && (
                              <button
                                type="button"
                                className="btn-remove"
                                onClick={() => handleRemoveSensorFromList(idx)}
                              >
                                Remove
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              <div className="form-actions">
                <button type="button" onClick={() => setStep(2)} className="btn-secondary">
                  Back to Configure
                </button>
                <button
                  type="button"
                  onClick={handleFinalSubmit}
                  disabled={!isAllConfigured || loading}
                  className="btn-primary"
                >
                  {loading ? 'Submitting...' : 'Create Equipment with Sensors'}
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export default EquipmentConfigModal;
