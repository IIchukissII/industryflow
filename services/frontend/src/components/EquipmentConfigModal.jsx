// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, useCallback } from 'react';
import Icon from './Icon';
import './EquipmentConfigModal.css';
import { createEquipment, updateEquipment, getEquipmentSensors, removeSensorFromEquipment, addSensorToEquipment, updateSensor } from '../services/equipmentApi';
import { sensorLabel } from '../utils/sensors';
import api from '../services/api';
import { errorText } from '../utils/errors';
import { wizardReading } from '../utils/reading-equipment-wizard';
import { humanize } from '../utils/humanize';

// An unconfigured slot: the machine declares more sensors than are set up.
const blankSensor = (position) => ({
  sensor_id: '', sensor_name: '', display_name: '', sensor_type: 'temperature', unit: 'celsius',
  description: '', position, is_critical: false, is_required_for_ml: true, normal_min: null, normal_max: null,
});

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
    expected_sensors: editEquipment.expected_sensors || []
  } : {
    equipment_id: '',
    equipment_type: 'pump',
    name: '',
    description: '',
    site_id: '',
    location: '',
    sensor_count: 1,
    expected_sensors: []
  });

  // Array of sensors to be added
  const [sensors, setSensors] = useState([]);
  
  // Current sensor being configured
  const [currentSensor, setCurrentSensor] = useState({
    sensor_id: '',
    sensor_name: '',
    display_name: '',
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

  const loadExistingSensors = useCallback(async (equipmentId, declared = 0) => {
    try {
      // A stored sensor keeps the label it was saved with, so only a changed one is written back.
      const data = (await getEquipmentSensors(equipmentId)).map((s) => ({
        ...s, display_name: s.display_name || '', saved_display_name: s.display_name || '',
      }));
      // Every declared sensor has a row, so a missing one can be set up from the review.
      const slots = Array.from({ length: Math.max(0, declared - data.length) }, (_, i) => blankSensor(data.length + i));
      setSensors([...data, ...slots]);
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
      (async () => { await loadExistingSensors(editEquipment.equipment_id, editEquipment.sensor_count); })();
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
        sensor_count: sensorCount
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
        sensor_name: '',
        display_name: '',
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
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  };

  const handleAddSensorToList = (e) => {
    e.preventDefault();
    setError(null);

    // Validate sensor_id and the key are not empty
    if (!currentSensor.sensor_id.trim()) {
      setError('Sensor ID is required');
      return;
    }
    if (!currentSensor.sensor_name.trim()) {
      setError('Sensor key is required');
      return;
    }
    if (currentSensor.display_name.trim().length > 100) {
      setError('Display name is at most 100 characters');
      return;
    }

    // Check for a duplicate sensor_id or key among the other sensors
    const others = sensors.filter((s, i) => i !== currentSensor.position);
    if (others.some(s => s.sensor_id === currentSensor.sensor_id)) {
      setError('Sensor ID already exists');
      return;
    }
    if (others.some(s => s.sensor_name && s.sensor_name === currentSensor.sensor_name)) {
      setError('Sensor key already exists on this machine');
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
        sensor_name: '',
        display_name: '',
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
      sensor_name: sensor.sensor_name || '',
      display_name: sensor.display_name || '',
      normal_min: sensor.normal_min != null ? sensor.normal_min : '',
      normal_max: sensor.normal_max != null ? sensor.normal_max : ''
    });
    setStep(2);
  };

  const handleRemoveSensorFromList = (position) => {
    // For edit mode with existing sensors
    if (isEditMode && sensors[position].created_at) {
      if (!window.confirm('Remove this sensor from database?')) return;
      removeSensorFromEquipment(createdEquipmentId, sensors[position].sensor_id)
        .then(() => loadExistingSensors(createdEquipmentId, parseInt(formData.sensor_count) || 0))
        .catch(err => setError(errorText(err)));
    } else {
      // Just remove from local array for new equipment
      const updatedSensors = [...sensors];
      updatedSensors[position] = {
        sensor_id: '',
        sensor_name: '',
        display_name: '',
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
        throw new Error(`Set up all ${formData.sensor_count} sensors before saving`);
      }

      // An empty label is sent as unset; the key is shown in its place.
      const body = (s) => ({ ...s, display_name: s.display_name.trim() || null });
      const stored = sensors.filter((s) => s.created_at);
      if (!stored.length) {
        // A new machine: all its sensors in one transaction.
        await api.post(`/api/equipment/${createdEquipmentId}/sensors/bulk`, {
          sensors: sensors.map(body)
        });
      } else {
        // Editing: stored sensors keep their key and change only their label; new ones are added.
        for (const s of stored) {
          if (s.display_name.trim() !== s.saved_display_name) {
            await updateSensor(createdEquipmentId, s.sensor_id, { display_name: s.display_name.trim() || null });
          }
        }
        for (const s of sensors.filter((x) => !x.created_at)) {
          await addSensorToEquipment(createdEquipmentId, body(s));
        }
      }

      onSuccess();
      handleClose();
    } catch (err) {
      setError(errorText(err));
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
      expected_sensors: []
    });
    setCurrentSensor({
      sensor_id: '',
      sensor_name: '',
      display_name: '',
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

  const total = parseInt(formData.sensor_count) || 0;
  const configuredCount = sensors.filter(s => s.sensor_id && s.sensor_id.trim() !== "").length;
  const isAllConfigured = configuredCount === total;
  const reading = wizardReading({
    step, editing: isEditMode, name: formData.name,
    position: currentSensor.position, total, configured: configuredCount,
  });
  const STEPS = ['Machine', 'Sensors', 'Review'];
  const optional = <span className="ecm-optional">optional</span>;

  return (
    <div className="modal-overlay" onClick={handleClose}>
      <div
        className="modal-content equipment-config-modal" role="dialog" aria-modal="true"
        aria-labelledby="ecm-title" onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-header">
          <h2 id="ecm-title">{isEditMode ? `Edit ${editEquipment?.name || formData.name}` : 'Add equipment'}</h2>
          <button type="button" className="modal-close" aria-label="Close" onClick={handleClose}>×</button>
        </div>

        <div className="modal-body">
          <ol className="ecm-steps" aria-label="Steps">
            {STEPS.map((label, i) => {
              const n = i + 1;
              const state = n === step ? 'is-current' : n < step ? 'is-done' : '';
              return (
                <li key={label} className="ecm-step-item">
                  {i > 0 && <span className="ecm-step-line" aria-hidden="true" />}
                  <span className={`ecm-step ${state}`} aria-current={n === step ? 'step' : undefined}>
                    <span className="ecm-step-n">{n < step ? <Icon name="check" size={13} /> : n}</span>
                    <span className="ecm-step-label">{label}</span>
                  </span>
                </li>
              );
            })}
          </ol>

          <p className="ecm-lede">{reading.lede}</p>
          {reading.sub && (
            <p className={`ecm-lede-sub ${reading.pending ? 'ecm-pending' : ''}`}>{reading.sub}</p>
          )}

          {error && <div className="ecm-error" role="alert">{error}</div>}

          {step === 1 && (
            <form onSubmit={handleStep1Submit} className="ecm-form">
              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ecm-equipment-id">Equipment ID</label>
                  <input
                    id="ecm-equipment-id"
                    type="text"
                    name="equipment_id"
                    className="mono"
                    value={formData.equipment_id}
                    onChange={handleInputChange}
                    placeholder="The UUID the device reports"
                    required
                    disabled={isEditMode}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ecm-equipment-type">Type</label>
                  <select
                    id="ecm-equipment-type"
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
                <label htmlFor="ecm-name">Name</label>
                <input
                  id="ecm-name"
                  type="text"
                  name="name"
                  value={formData.name}
                  onChange={handleInputChange}
                  placeholder="e.g., Main circulation pump"
                  required
                />
              </div>

              <div className="form-group">
                <label htmlFor="ecm-description">Description{optional}</label>
                <textarea
                  id="ecm-description"
                  name="description"
                  value={formData.description}
                  onChange={handleInputChange}
                  rows="3"
                />
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ecm-site">Site{optional}</label>
                  <input
                    id="ecm-site"
                    type="text"
                    name="site_id"
                    value={formData.site_id}
                    onChange={handleInputChange}
                    placeholder="e.g., SITE-A"
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ecm-location">Location{optional}</label>
                  <input
                    id="ecm-location"
                    type="text"
                    name="location"
                    value={formData.location}
                    onChange={handleInputChange}
                    placeholder="e.g., Building 3, floor 2"
                  />
                </div>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ecm-sensor-count">Sensors</label>
                  <input
                    id="ecm-sensor-count"
                    type="number"
                    name="sensor_count"
                    className="mono"
                    value={formData.sensor_count}
                    onChange={handleInputChange}
                    min="1"
                    max="100"
                    required
                  />
                  <small>How many sensors report from this machine, 1 to 100</small>
                </div>
              </div>

              <div className="form-actions">
                <button type="button" onClick={handleClose} className="btn">Cancel</button>
                <button type="submit" disabled={loading} className="btn-primary">
                  {loading ? 'Saving…' : 'Next: sensors'}
                </button>
              </div>
            </form>
          )}

          {step === 2 && (
            <form onSubmit={handleAddSensorToList} className="ecm-form">
              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ecm-sensor-id">Sensor ID</label>
                  <input
                    id="ecm-sensor-id"
                    type="text"
                    name="sensor_id"
                    className="mono"
                    value={currentSensor.sensor_id}
                    onChange={handleSensorInputChange}
                    placeholder="The UUID the device reports"
                    required
                    disabled={!!currentSensor.created_at}
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ecm-sensor-key">Key</label>
                  <input
                    id="ecm-sensor-key"
                    type="text"
                    name="sensor_name"
                    className="mono"
                    value={currentSensor.sensor_name}
                    onChange={handleSensorInputChange}
                    placeholder="e.g., temp_root_1"
                    required
                    disabled={!!currentSensor.created_at}
                  />
                  <small>Models, features and rule patterns refer to it; it cannot change</small>
                </div>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ecm-display-name">Display name{optional}</label>
                  <input
                    id="ecm-display-name"
                    type="text"
                    name="display_name"
                    value={currentSensor.display_name}
                    onChange={handleSensorInputChange}
                    placeholder="e.g., Root zone temperature"
                    maxLength={100}
                  />
                  <small>What people read; empty shows the key</small>
                </div>
                <div className="form-group">
                  <label htmlFor="ecm-sensor-type">Type</label>
                  <select
                    id="ecm-sensor-type"
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
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ecm-unit">Unit{optional}</label>
                  <input
                    id="ecm-unit"
                    type="text"
                    name="unit"
                    value={currentSensor.unit}
                    onChange={handleSensorInputChange}
                    placeholder="e.g., celsius, bar, m/s"
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ecm-sensor-description">Description{optional}</label>
                  <input
                    id="ecm-sensor-description"
                    type="text"
                    name="description"
                    value={currentSensor.description}
                    onChange={handleSensorInputChange}
                  />
                </div>
              </div>

              <div className="form-row">
                <div className="form-group">
                  <label htmlFor="ecm-normal-min">Normal minimum{optional}</label>
                  <input
                    id="ecm-normal-min"
                    type="number"
                    step="0.01"
                    name="normal_min"
                    className="mono"
                    value={currentSensor.normal_min}
                    onChange={handleSensorInputChange}
                    placeholder="e.g., 20.0"
                  />
                </div>
                <div className="form-group">
                  <label htmlFor="ecm-normal-max">Normal maximum{optional}</label>
                  <input
                    id="ecm-normal-max"
                    type="number"
                    step="0.01"
                    name="normal_max"
                    className="mono"
                    value={currentSensor.normal_max}
                    onChange={handleSensorInputChange}
                    placeholder="e.g., 80.0"
                  />
                </div>
              </div>

              <div className="form-row">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    name="is_critical"
                    checked={currentSensor.is_critical}
                    onChange={handleSensorInputChange}
                  />
                  <span>Critical sensor</span>
                </label>
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    name="is_required_for_ml"
                    checked={currentSensor.is_required_for_ml}
                    onChange={handleSensorInputChange}
                  />
                  <span>Required by models</span>
                </label>
              </div>

              <div className="form-actions">
                <button type="button" onClick={() => setStep(1)} className="btn ecm-actions-start">Back</button>
                {configuredCount > 0 && currentSensor.position < total - 1 && (
                  <button type="button" onClick={() => setStep(3)} className="btn">Review</button>
                )}
                <button type="submit" className="btn-primary">
                  {currentSensor.position < total - 1 ? 'Next sensor' : 'Review'}
                </button>
              </div>
            </form>
          )}

          {step === 3 && (
            <>
              <div className="ecm-table-wrap">
                <table className="data-table ecm-table">
                  <thead>
                    <tr>
                      <th className="ecm-pos">#</th>
                      <th>Sensor</th>
                      <th>Type</th>
                      <th>Unit</th>
                      <th>Critical</th>
                      <th aria-label="Actions" />
                    </tr>
                  </thead>
                  <tbody>
                    {sensors.map((sensor, idx) => (
                      <tr key={idx} className={!sensor.sensor_id ? 'ecm-row-pending' : ''}>
                        <td className="ecm-pos mono">{sensor.position + 1}</td>
                        <td>
                          {sensor.sensor_id
                            ? <>
                              {sensorLabel(sensor)}
                              {sensor.display_name && sensor.sensor_name && (
                                <span className="ecm-key mono">{sensor.sensor_name}</span>
                              )}
                            </>
                            : 'Not set up'}
                        </td>
                        <td>{sensor.sensor_id ? humanize(sensor.sensor_type) : ''}</td>
                        <td>{sensor.sensor_id ? (sensor.unit || '–') : ''}</td>
                        <td>{sensor.is_critical ? 'Yes' : ''}</td>
                        <td>
                          <div className="ecm-row-actions">
                            <button type="button" className="btn btn-sm" onClick={() => handleEditSensor(idx)}>
                              {sensor.sensor_id ? 'Edit' : 'Set up'}
                            </button>
                            {sensor.sensor_id && (
                              <button type="button" className="btn-danger btn-sm" onClick={() => handleRemoveSensorFromList(idx)}>
                                Remove
                              </button>
                            )}
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="form-actions">
                <button type="button" onClick={() => setStep(2)} className="btn ecm-actions-start">Back</button>
                <button
                  type="button"
                  onClick={handleFinalSubmit}
                  disabled={!isAllConfigured || loading}
                  className="btn-primary"
                >
                  {loading ? 'Saving…' : isEditMode ? 'Save sensors' : 'Add equipment'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default EquipmentConfigModal;
