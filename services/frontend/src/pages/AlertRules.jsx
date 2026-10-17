// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, useCallback, useId } from 'react';
import './AlertRules.css';
import authFetch from '../services/http';
import { canWrite } from '../utils/roles';
import { alertRulesReading, kindWord, RULE_KINDS, ruleSentenceParts } from '../utils/reading-alertrules';
import { sensorLabel } from '../utils/sensors';

const RANGE = ['between', 'outside_range'];

const CONDITIONS = [
  { value: 'greater_than', label: 'Above' },
  { value: 'less_than', label: 'Below' },
  { value: 'equals', label: 'Equal to' },
  { value: 'between', label: 'Between' },
  { value: 'outside_range', label: 'Outside a range' },
];

const SEVERITIES = ['low', 'medium', 'high', 'critical'];

const cap = (s) => (s ? s.charAt(0).toUpperCase() + s.slice(1) : '');

// What the rule watches and when it fires, as one sentence: names, with values in mono.
function RuleSentence({ rule }) {
  return ruleSentenceParts(rule).map((p, i) => (p.mono
    ? <span key={i} className="mono" title={p.title}>{p.t}</span>
    : <span key={i}>{p.t}</span>));
}

function AlertRules() {
  // Creating, editing and deleting rules needs engineer or admin (observer is read-only).
  const [writable] = useState(() => canWrite());
  const [kind, setKind] = useState('all');
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [editingRule, setEditingRule] = useState(null);
  const [showForm, setShowForm] = useState(false);

  const fetchRules = useCallback(async () => {
    try {
      const response = await authFetch('/api/alert-rules?enabled_only=false');
      if (!response.ok) throw new Error(`the server answered ${response.status}`);
      setRules(await response.json());
      setLoadError(null);
    } catch (error) {
      setLoadError(error.message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Fetch on mount. Invoked from an inner async function so the effect body itself runs no
  // synchronous setState — the rules land off the sync path when the request resolves.
  useEffect(() => {
    (async () => { await fetchRules(); })();
  }, [fetchRules]);

  const handleNew = () => {
    setEditingRule(null);
    setShowForm(true);
  };

  const handleEdit = (rule) => {
    setEditingRule(rule);
    setShowForm(true);
  };

  const handleCloseForm = () => {
    setEditingRule(null);
    setShowForm(false);
  };

  // Resolves to an error message for the form to show, or null once saved.
  const handleSaveRule = async (payload) => {
    try {
      const response = payload.rule_id
        ? await authFetch(`/api/alert-rules/${payload.rule_id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        })
        : await authFetch('/api/alert-rules', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        const detail = typeof body.detail === 'string' ? body.detail : `the server answered ${response.status}`;
        return `The rule was not saved: ${detail}. Correct it and save again.`;
      }
      await fetchRules();
      handleCloseForm();
      setNotice(null);
      return null;
    } catch (error) {
      return `The rule was not saved: ${error.message}. Check the connection and save again.`;
    }
  };

  const handleDelete = async (rule) => {
    if (!window.confirm(`Delete the rule ${rule.name}? It stops raising alerts; past alerts stay.`)) return;
    try {
      const response = await authFetch(`/api/alert-rules/${rule.rule_id}`, { method: 'DELETE' });
      if (!response.ok) throw new Error(`the server answered ${response.status}`);
      setNotice(null);
      await fetchRules();
    } catch (error) {
      setNotice(`${rule.name} was not deleted: ${error.message}. Try again.`);
    }
  };

  if (loading) return <div className="ar-state"><span className="sdot pending" /> Loading alert rules</div>;
  if (loadError) {
    return (
      <div className="ar-state ar-state-bad" role="alert">
        The alert rules could not load: {loadError}.
        <button type="button" className="btn" onClick={() => { setLoading(true); fetchRules(); }}>Try again</button>
      </div>
    );
  }

  const countOf = (type) => rules.filter((r) => r.detection_type === type).length;
  const shown = kind === 'all' ? rules : rules.filter((r) => r.detection_type === kind);

  return (
    <div className="ar">
      <div className="ar-head">
        <p className="reading">{alertRulesReading({ rules })}</p>
        {writable && (
          <button type="button" className="btn btn-primary ar-add" onClick={handleNew}>New rule</button>
        )}
      </div>

      {notice && <p className="ar-notice" role="alert">{notice}</p>}

      {rules.length > 0 && (
        <section className="ar-rules" aria-labelledby="ar-rules-h">
          <div className="ar-rules-head">
            <h2 id="ar-rules-h" className="ar-h">Rules</h2>
            <div className="seg" role="group" aria-label="Show rules of one kind">
              <button type="button" aria-pressed={kind === 'all'} onClick={() => setKind('all')}>
                All <span className="mono ar-seg-n">{rules.length}</span>
              </button>
              {RULE_KINDS.map((k) => (
                <button key={k.type} type="button" aria-pressed={kind === k.type} onClick={() => setKind(k.type)}>
                  {cap(k.word)} <span className="mono ar-seg-n">{countOf(k.type)}</span>
                </button>
              ))}
            </div>
          </div>

          {shown.length === 0 ? (
            <p className="ar-empty">No {kindWord(kind)} rules.</p>
          ) : (
            <ul className="ar-list">
              {shown.map((rule) => (
                <li key={rule.rule_id} className={`ar-rule${rule.enabled ? '' : ' is-off'}`}>
                  <div className="ar-rule-main">
                    <h3 className="ar-rule-name">
                      {rule.name}
                      {!rule.enabled && <span className="badge ar-off">Off</span>}
                    </h3>
                    {rule.description && <p className="ar-rule-desc">{rule.description}</p>}
                    <p className="ar-rule-what">
                      <RuleSentence rule={rule} />
                    </p>
                  </div>
                  {writable && (
                    <div className="ar-rule-actions">
                      <button type="button" className="btn" onClick={() => handleEdit(rule)}>Edit</button>
                      <button type="button" className="btn btn-danger" onClick={() => handleDelete(rule)}>Delete</button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {showForm && (
        <EditRuleModal
          rule={editingRule}
          onClose={handleCloseForm}
          onSave={handleSaveRule}
        />
      )}
    </div>
  );
}

// A segmented choice inside the form: buttons, never a submit.
function Seg({ label, value, options, onChange }) {
  return (
    <div className="seg ar-seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  );
}

// The create/edit form.
function EditRuleModal({ rule, onClose, onSave }) {
  const uid = useId();
  const fid = (name) => `${uid}-${name}`;
  const [formData, setFormData] = useState({
    rule_id: rule?.rule_id,
    company_id: rule?.company_id || '',
    name: rule?.name || '',
    description: rule?.description || '',
    detection_type: rule?.detection_type || 'threshold',
    sensor_id: rule?.sensor_id || '',
    sensor_pattern: rule?.sensor_pattern || '',
    equipment_id: rule?.equipment_id || '',
    site_id: rule?.site_id || '',
    condition: rule?.condition || 'greater_than',
    // ?? not ||: a stored threshold of 0 is a value, not a blank.
    threshold: rule?.threshold ?? '',
    threshold_min: rule?.threshold_min ?? '',
    threshold_max: rule?.threshold_max ?? '',
    model_id: rule?.model_id || '',
    severity: rule?.severity || 'medium',
    priority: rule?.priority || 0,
    enabled: rule?.enabled !== false,
  });
  const set = (patch) => setFormData((f) => ({ ...f, ...patch }));

  const [ruleLevel, setRuleLevel] = useState(
    rule?.equipment_id ? 'equipment' : rule?.sensor_id ? 'sensor' : 'pattern'
  );
  const [sensors, setSensors] = useState([]);
  const [equipment, setEquipment] = useState([]);
  const [models, setModels] = useState([]);
  const [loadingData, setLoadingData] = useState(true); // always fetches on mount
  const [formError, setFormError] = useState(null);
  const [saving, setSaving] = useState(false);

  const fetchSensorsAndEquipment = useCallback(async () => {
    setLoadingData(true);
    try {
      // Fetch sensors from cache
      const sensorsResponse = await authFetch('/api/cache/sensors');
      if (sensorsResponse.ok) {
        const sensorsData = await sensorsResponse.json();
        const sensorsList = Object.entries(sensorsData.sensors || {}).map(([id, data]) => ({
          sensor_id: id,
          sensor_name: data.sensor_name,
          display_name: data.display_name,
          equipment_name: data.equipment_name
        }));
        setSensors(sensorsList);
      }

      // Fetch equipment
      const equipmentResponse = await authFetch('/api/equipment');
      if (equipmentResponse.ok) {
        const equipmentData = await equipmentResponse.json();
        setEquipment(equipmentData);
      }

      // Fetch models — the target a drift (statistical) rule watches.
      const modelsResponse = await authFetch('/api/models');
      if (modelsResponse.ok) {
        const modelsData = await modelsResponse.json();
        setModels(modelsData.models || []);
      }
    } catch (error) {
      console.error('Error fetching sensors/equipment:', error);
    } finally {
      setLoadingData(false);
    }
  }, []);

  // Fetch on mount. Invoked from an inner async function so the effect body itself runs no
  // synchronous setState — the sensor/equipment/model lists land off the sync path on resolve.
  useEffect(() => {
    (async () => { await fetchSensorsAndEquipment(); })();
  }, [fetchSensorsAndEquipment]);

  // Escape closes the form.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setFormError(null);

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
      setFormError('Choose what the rule watches: a sensor pattern, a sensor or a machine.');
      return;
    }

    if (formData.detection_type === 'threshold') {
      payload.condition = formData.condition;
      if (RANGE.includes(formData.condition)) {
        payload.threshold_min = parseFloat(formData.threshold_min);
        payload.threshold_max = parseFloat(formData.threshold_max);
      } else {
        payload.threshold = parseFloat(formData.threshold);
      }
    }

    if (formData.detection_type === 'ml') {
      // An ML rule is scored by a model; the server refuses one without it.
      if (!formData.model_id) {
        setFormError('Choose the model this rule is scored by.');
        return;
      }
      payload.model_id = formData.model_id;
    }
    if (formData.detection_type === 'statistical') {
      // Model-drift rule (ADR-0021): bind the model to watch and, optionally, the
      // drifted-feature share (0–1) that trips the alert. Left blank, the evaluator
      // applies the service default.
      if (!formData.model_id) {
        setFormError('Choose the model this drift rule watches.');
        return;
      }
      payload.model_id = formData.model_id;
      if (formData.threshold !== '' && formData.threshold !== null) {
        payload.threshold = parseFloat(formData.threshold);
      }
    }

    if (rule) {
      payload.rule_id = formData.rule_id;
    }

    setSaving(true);
    const err = await onSave(payload);
    setSaving(false);
    if (err) setFormError(err);
  };

  const handleRuleLevelChange = (level) => {
    setRuleLevel(level);
    // Clear all target fields when switching levels
    set({ sensor_id: '', equipment_id: '', sensor_pattern: '' });
  };

  // A rule being edited keeps its stored target and model selectable, even when the live
  // lists (sensor cache, equipment, models) do not carry it: a required select with no
  // matching option would block saving.
  const missing = (list, key, value) => Boolean(value) && !list.some((x) => x[key] === value);
  const isRange = RANGE.includes(formData.condition);
  const needsModel = formData.detection_type === 'statistical' || formData.detection_type === 'ml';

  return (
    <div className="ar-modal-overlay" onClick={onClose}>
      <div
        className="ar-modal" role="dialog" aria-modal="true" aria-labelledby={fid('title')}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="ar-modal-head">
          <h2 id={fid('title')}>{rule ? `Edit ${rule.name}` : 'New rule'}</h2>
          <button type="button" className="ar-modal-close" aria-label="Close" onClick={onClose}>×</button>
        </div>

        <form className="ar-form" onSubmit={handleSubmit}>
          <div className="ar-field">
            <label htmlFor={fid('name')}>Name</label>
            <input
              id={fid('name')}
              type="text"
              value={formData.name}
              onChange={(e) => set({ name: e.target.value })}
              required
            />
          </div>

          <div className="ar-field">
            <label htmlFor={fid('desc')}>Description</label>
            <input
              id={fid('desc')}
              type="text"
              value={formData.description}
              onChange={(e) => set({ description: e.target.value })}
            />
          </div>

          <fieldset className="ar-group">
            <legend>Watches</legend>
            <Seg
              label="What the rule watches"
              value={ruleLevel}
              onChange={handleRuleLevelChange}
              options={[
                { value: 'pattern', label: 'Sensor pattern' },
                { value: 'sensor', label: 'One sensor' },
                { value: 'equipment', label: 'A machine' },
              ]}
            />

            {ruleLevel === 'pattern' && (
              <div className="ar-field">
                <label htmlFor={fid('pattern')}>Sensor pattern</label>
                <input
                  id={fid('pattern')}
                  type="text"
                  className="mono"
                  value={formData.sensor_pattern}
                  onChange={(e) => set({ sensor_pattern: e.target.value })}
                  placeholder="temp_*"
                  aria-describedby={fid('pattern-hint')}
                  required
                />
                <p id={fid('pattern-hint')} className="ar-hint">
                  * matches any run of characters: <span className="mono">temp_*</span> matches every
                  sensor whose id starts with temp_, <span className="mono">*_A1</span> every one ending in _A1.
                </p>
              </div>
            )}

            {ruleLevel === 'sensor' && (
              <div className="ar-field">
                <label htmlFor={fid('sensor')}>Sensor</label>
                {loadingData ? (
                  <p className="ar-hint">Loading sensors</p>
                ) : (
                  <select
                    id={fid('sensor')}
                    value={formData.sensor_id}
                    onChange={(e) => set({ sensor_id: e.target.value })}
                    required
                  >
                    <option value="">Choose a sensor</option>
                    {missing(sensors, 'sensor_id', formData.sensor_id) && (
                      <option value={formData.sensor_id}>{sensorLabel(rule, formData.sensor_id)} (not reporting now)</option>
                    )}
                    {sensors.map(sensor => (
                      <option key={sensor.sensor_id} value={sensor.sensor_id}>
                        {sensorLabel(sensor)}
                        {sensor.display_name && sensor.sensor_name ? ` (${sensor.sensor_name})` : ''}
                        {sensor.equipment_name && `, ${sensor.equipment_name}`}
                      </option>
                    ))}
                  </select>
                )}
                {sensors.length === 0 && !loadingData && (
                  <p className="ar-hint ar-hint-warn">No sensor is reporting. A sensor appears here once its device sends a reading.</p>
                )}
              </div>
            )}

            {ruleLevel === 'equipment' && (
              <div className="ar-field">
                <label htmlFor={fid('equipment')}>Machine</label>
                {loadingData ? (
                  <p className="ar-hint">Loading equipment</p>
                ) : (
                  <select
                    id={fid('equipment')}
                    value={formData.equipment_id}
                    onChange={(e) => set({ equipment_id: e.target.value })}
                    required
                  >
                    <option value="">Choose a machine</option>
                    {missing(equipment, 'equipment_id', formData.equipment_id) && (
                      <option value={formData.equipment_id}>{rule?.equipment_name || formData.equipment_id}</option>
                    )}
                    {equipment.map(eq => (
                      <option key={eq.equipment_id} value={eq.equipment_id}>
                        {eq.name} ({eq.equipment_id}), {eq.equipment_type}
                      </option>
                    ))}
                  </select>
                )}
                {equipment.length === 0 && !loadingData && (
                  <p className="ar-hint ar-hint-warn">No machines yet. <a href="/equipment">Add one on Equipment</a> first.</p>
                )}
              </div>
            )}
          </fieldset>

          <fieldset className="ar-group">
            <legend>Detection</legend>
            <Seg
              label="How the rule detects"
              value={formData.detection_type}
              onChange={(v) => set({ detection_type: v })}
              options={[
                { value: 'threshold', label: 'Threshold' },
                { value: 'ml', label: 'Model score' },
                { value: 'statistical', label: 'Model drift' },
              ]}
            />

            {needsModel && (
              <div className="ar-field">
                <label htmlFor={fid('model')}>{formData.detection_type === 'ml' ? 'Scored by model' : 'Model to watch'}</label>
                {loadingData ? (
                  <p className="ar-hint">Loading models</p>
                ) : (
                  <select
                    id={fid('model')}
                    value={formData.model_id}
                    onChange={(e) => set({ model_id: e.target.value })}
                    required
                  >
                    <option value="">Choose a model</option>
                    {missing(models, 'model_id', formData.model_id) && (
                      <option value={formData.model_id}>{rule?.model_name || formData.model_id}</option>
                    )}
                    {models.map(model => (
                      <option key={model.model_id} value={model.model_id}>
                        {model.model_name || model.name} ({model.status})
                      </option>
                    ))}
                  </select>
                )}
                {models.length === 0 && !loadingData && (
                  <p className="ar-hint ar-hint-warn">No models yet. <a href="/ml-models">Register one on Models</a> before adding a model rule.</p>
                )}
              </div>
            )}

            {formData.detection_type === 'statistical' && (
              <div className="ar-field">
                <label htmlFor={fid('drift')}>Drifted share that fires</label>
                <input
                  id={fid('drift')}
                  type="number"
                  className="mono ar-num"
                  step="0.01"
                  min="0"
                  max="1"
                  value={formData.threshold}
                  onChange={(e) => set({ threshold: e.target.value })}
                  placeholder="0.30"
                  aria-describedby={fid('drift-hint')}
                />
                <p id={fid('drift-hint')} className="ar-hint">
                  The rule fires when the share of drifted features (0 to 1) exceeds this: the live
                  data has moved from the model&apos;s training baseline. Leave it blank for the
                  service default.
                </p>
              </div>
            )}

            {formData.detection_type === 'threshold' && (
              <div className="ar-row">
                <div className="ar-field">
                  <label htmlFor={fid('cond')}>Fires on a reading</label>
                  <select
                    id={fid('cond')}
                    value={formData.condition}
                    onChange={(e) => set({ condition: e.target.value })}
                  >
                    {CONDITIONS.map((c) => <option key={c.value} value={c.value}>{c.label}</option>)}
                  </select>
                </div>

                {isRange ? (
                  <>
                    <div className="ar-field">
                      <label htmlFor={fid('min')}>From</label>
                      <input
                        id={fid('min')}
                        type="number"
                        className="mono ar-num"
                        step="0.01"
                        value={formData.threshold_min}
                        onChange={(e) => set({ threshold_min: e.target.value })}
                        required
                      />
                    </div>
                    <div className="ar-field">
                      <label htmlFor={fid('max')}>To</label>
                      <input
                        id={fid('max')}
                        type="number"
                        className="mono ar-num"
                        step="0.01"
                        value={formData.threshold_max}
                        onChange={(e) => set({ threshold_max: e.target.value })}
                        required
                      />
                    </div>
                  </>
                ) : (
                  <div className="ar-field">
                    <label htmlFor={fid('value')}>Value</label>
                    <input
                      id={fid('value')}
                      type="number"
                      className="mono ar-num"
                      step="0.01"
                      value={formData.threshold}
                      onChange={(e) => set({ threshold: e.target.value })}
                      required
                    />
                  </div>
                )}
              </div>
            )}
          </fieldset>

          <fieldset className="ar-group">
            <legend>Severity</legend>
            <Seg
              label="Severity"
              value={formData.severity}
              onChange={(v) => set({ severity: v })}
              options={SEVERITIES.map((s) => ({ value: s, label: cap(s) }))}
            />
          </fieldset>

          <label className="ar-check" htmlFor={fid('enabled')}>
            <input
              id={fid('enabled')}
              type="checkbox"
              checked={formData.enabled}
              onChange={(e) => set({ enabled: e.target.checked })}
            />
            Active: the rule raises alerts
          </label>

          {formError && <p className="ar-form-error" role="alert">{formError}</p>}

          <div className="ar-form-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={saving}>
              {saving ? 'Saving' : rule ? 'Save rule' : 'Create rule'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export default AlertRules;
