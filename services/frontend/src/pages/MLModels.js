// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import React, { useState, useEffect } from 'react';
import Header from '../components/Header';
import './MLModels.css';

const ML_API_BASE = 'http://localhost:8002';

function MLModels() {
  const [user, setUser] = useState(null);
  const [connected, setConnected] = useState(false);
  const [activeTab, setActiveTab] = useState('models');
  const [models, setModels] = useState([]);
  const [featureConfigs, setFeatureConfigs] = useState({});
  const [experiments, setExperiments] = useState([]);
  const [selectedExperiment, setSelectedExperiment] = useState(null);
  const [runs, setRuns] = useState([]);
  const [loading, setLoading] = useState(true);
  const [mlHealth, setMlHealth] = useState(null);
  const [showTrainingModal, setShowTrainingModal] = useState(false);
  const [trainingConfig, setTrainingConfig] = useState({
    sensor_id: 'all_sensors',
    sensor_type: 'multivariate',
    contamination: 0.1,
    n_estimators: 100,
    lookback_days: 30,
    description: ''
  });
  useEffect(() => {
    const userData = localStorage.getItem('user');
    if (userData) {
      setUser(JSON.parse(userData));
    }

    // Check API connection
    fetch('http://localhost:8000/health')
      .then(res => res.json())
      .then(() => setConnected(true))
      .catch(() => setConnected(false));

    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    const token = localStorage.getItem('access_token');

    try {
      const healthRes = await fetch(`${ML_API_BASE}/health`);
      const healthData = await healthRes.json();
      setMlHealth(healthData);

      const modelsRes = await fetch(`${ML_API_BASE}/api/models`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const modelsData = await modelsRes.json();
      const modelsList = modelsData.models || [];
      setModels(modelsList);

      // Fetch feature configs for models that have them
      await fetchFeatureConfigs(modelsList, token);

      const expRes = await fetch(`${ML_API_BASE}/api/mlflow/experiments`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const expData = await expRes.json();
      setExperiments(expData.experiments || []);

    } catch (error) {
      console.error('Error fetching ML data:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchFeatureConfigs = async (models, token) => {
    const configs = {};

    for (const model of models) {
      if (model.feature_config_id) {
        try {
          const res = await fetch(`${ML_API_BASE}/api/feature-configs/${model.feature_config_id}`, {
            headers: { 'Authorization': `Bearer ${token}` }
          });
          if (res.ok) {
            const data = await res.json();
            configs[model.feature_config_id] = data;
          }
        } catch (error) {
          console.error(`Error fetching feature config ${model.feature_config_id}:`, error);
        }
      }
    }

    setFeatureConfigs(configs);
  };

  const fetchRuns = async (experimentId) => {
    const token = localStorage.getItem('access_token');
    try {
      const res = await fetch(`${ML_API_BASE}/api/mlflow/experiments/${experimentId}/runs`, {
        headers: { 'Authorization': `Bearer ${token}` }
      });
      const data = await res.json();
      setRuns(data.runs || []);
      setSelectedExperiment(experimentId);
    } catch (error) {
      console.error('Error fetching runs:', error);
    }
  };

  const changeModelStatus = async (modelId, newStatus) => {
    const token = localStorage.getItem('access_token');
    try {
      const res = await fetch(`${ML_API_BASE}/api/models/${modelId}/deploy`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          model_id: modelId,
          environment: newStatus
        })
      });

      if (res.ok) {
        alert(`Model status changed to ${newStatus}`);
        fetchData();
      } else {
        const error = await res.json();
        alert(`Failed to change status: ${error.detail}`);
      }
    } catch (error) {
      console.error('Error changing model status:', error);
      alert('Error changing model status');
    }
  };

  const handleTrainingSubmit = async (e) => {
    e.preventDefault();
    
    const token = localStorage.getItem('access_token');
    const userData = JSON.parse(localStorage.getItem('user'));
    
    if (!userData || !userData.company_id) {
      alert('Unable to get company information. Please log in again.');
      return;
    }

    try {
      const res = await fetch(`${ML_API_BASE}/api/training/start`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${token}`,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          config: {
            company_id: userData.company_id,
            sensor_id: trainingConfig.sensor_id,
            sensor_type: trainingConfig.sensor_type,
            contamination: parseFloat(trainingConfig.contamination),
            n_estimators: parseInt(trainingConfig.n_estimators),
            lookback_days: parseInt(trainingConfig.lookback_days)
          },
          description: trainingConfig.description || 'Training started from UI'
        })
      });

      if (res.ok) {
        const data = await res.json();
        alert(`Training started successfully!\n\nJob ID: ${data.job_id}\nStatus: ${data.status}\n\nCheck back in a few minutes to see the new model.`);
        setShowTrainingModal(false);
        
        // Reset form
        setTrainingConfig({
          sensor_id: 'all_sensors',
          sensor_type: 'multivariate',
          contamination: 0.1,
          n_estimators: 100,
          lookback_days: 30,
          description: ''
        });
        
        // Refresh models list after a delay
        setTimeout(() => {
          fetchData();
        }, 2000);
      } else {
        const error = await res.json();
        alert(`Training failed: ${error.detail || JSON.stringify(error)}`);
      }
    } catch (error) {
      console.error('Error starting training:', error);
      alert('Error starting training: ' + error.message);
    }
  };



  const formatDate = (dateString) => {
    return new Date(dateString).toLocaleString();
  };

  const formatMetric = (value) => {
    if (value === null || value === undefined) return 'N/A';
    return typeof value === 'number' ? value.toFixed(4) : value;
  };

  const renderMetadataTable = (metadata, model) => {
    if (!metadata) return null;

    const rows = [];

    if (metadata.mlflow) {
      rows.push({ section: 'MLflow', key: 'Run ID', value: metadata.mlflow.run_id });
      rows.push({ section: 'MLflow', key: 'Model URI', value: metadata.mlflow.model_uri });
      rows.push({ section: 'MLflow', key: 'Experiment ID', value: metadata.mlflow.experiment_id });
    }

    if (metadata.sensor_id) rows.push({ section: 'Sensor', key: 'Sensor ID', value: metadata.sensor_id });
    if (metadata.sensor_type) rows.push({ section: 'Sensor', key: 'Type', value: metadata.sensor_type });
    if (metadata.feature_count) rows.push({ section: 'Sensor', key: 'Features', value: metadata.feature_count });
    if (metadata.training_samples) rows.push({ section: 'Training', key: 'Samples', value: metadata.training_samples.toLocaleString() });

    // Add feature engineering information
    if (model && model.feature_config_id && featureConfigs[model.feature_config_id]) {
      const config = featureConfigs[model.feature_config_id];
      rows.push({ section: 'Feature Engineering', key: 'Config Name', value: config.name });
      rows.push({ section: 'Feature Engineering', key: 'Equipment Type', value: config.equipment_type });
      rows.push({ section: 'Feature Engineering', key: 'Base Sensors', value: config.base_sensors?.length || 0 });
      rows.push({ section: 'Feature Engineering', key: 'Total Features', value: config.transformations?.length || 0 });
      rows.push({ section: 'Feature Engineering', key: 'Version', value: config.version || 'N/A' });

      // Count transformation types
      if (config.transformations && config.transformations.length > 0) {
        const typeCounts = {};
        config.transformations.forEach(t => {
          typeCounts[t.type] = (typeCounts[t.type] || 0) + 1;
        });
        Object.entries(typeCounts).forEach(([type, count]) => {
          rows.push({
            section: 'Feature Engineering',
            key: `${type.charAt(0).toUpperCase() + type.slice(1)} Transforms`,
            value: count
          });
        });
      }
    }

    if (metadata.hyperparameters) {
      Object.entries(metadata.hyperparameters).forEach(([key, value]) => {
        rows.push({
          section: 'Hyperparameters',
          key: key.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase()),
          value: typeof value === 'number' ? value : String(value)
        });
      });
    }

    let currentSection = '';

    return (
      <table className="metadata-table">
        <tbody>
          {rows.map((row, idx) => {
            const showSection = row.section !== currentSection;
            currentSection = row.section;
            return (
              <React.Fragment key={idx}>
                {showSection && (
                  <tr className="section-header">
                    <td colSpan="2">{row.section}</td>
                  </tr>
                )}
                <tr>
                  <td className="metadata-key">{row.key}</td>
                  <td className="metadata-value">{row.value}</td>
                </tr>
              </React.Fragment>
            );
          })}
        </tbody>
      </table>
    );
  };

  return (
    <div className="App">
      <Header user={user} connected={connected} />

      <div className="ml-models-container">
        <div className="ml-header">
          <div>
            <h1>ML Model Management</h1>
            <p className="ml-subtitle">Train, deploy, and monitor machine learning models for anomaly detection</p>
          </div>
          <button className="btn-primary" onClick={() => setShowTrainingModal(true)}>
            🚀 Start Training
          </button>
        </div>

        {mlHealth && (
          <div className="ml-health-card">
            <h3>MLflow System Status</h3>
            <div className="health-stats">
              <div className="health-stat">
                <span className="stat-label">Status:</span>
                <span className={`stat-value ${mlHealth.status === 'healthy' ? 'healthy' : 'error'}`}>
                  {mlHealth.status}
                </span>
              </div>
              <div className="health-stat">
                <span className="stat-label">Models in Storage:</span>
                <span className="stat-value">{mlHealth.model_count}</span>
              </div>
              <div className="health-stat">
                <span className="stat-label">MLflow URI:</span>
                <span className="stat-value">{mlHealth.mlflow_uri}</span>
              </div>
            </div>
          </div>
        )}

        <div className="ml-tabs">
          <button
            className={`ml-tab ${activeTab === 'models' ? 'active' : ''}`}
            onClick={() => setActiveTab('models')}
          >
            Deployed Models ({models.length})
          </button>
          <button
            className={`ml-tab ${activeTab === 'experiments' ? 'active' : ''}`}
            onClick={() => setActiveTab('experiments')}
          >
            Experiments ({experiments.length})
          </button>
        </div>

        <div className="ml-tab-content">
          {loading ? (
            <div className="ml-loading">Loading...</div>
          ) : activeTab === 'models' ? (
            <div className="models-section">
              {models.length === 0 ? (
                <div className="empty-state">
                  <p>No models deployed yet</p>
                  <button className="btn-secondary" onClick={() => setShowTrainingModal(true)}>
                    Train your first model
                  </button>
                </div>
              ) : (
                <div className="models-grid">
                  {models.map(model => (
                    <div key={model.model_id} className="model-card">
                      <div className="model-header">
                        <h3>{model.name}</h3>
                        <span className={`status-badge ${model.status}`}>
                          {model.status}
                        </span>
                      </div>
                      
                      <p className="model-description">{model.description}</p>
                      
                      <div className="model-meta">
                        <div className="meta-item">
                          <span className="meta-label">Type:</span>
                          <span className="meta-value">{model.model_type}</span>
                        </div>
                        <div className="meta-item">
                          <span className="meta-label">Version:</span>
                          <span className="meta-value">v{model.version}</span>
                        </div>
                        {model.equipment_type && (
                          <div className="meta-item">
                            <span className="meta-label">Equipment:</span>
                            <span className="meta-value">{model.equipment_type}</span>
                          </div>
                        )}
                        <div className="meta-item">
                          <span className="meta-label">Trained:</span>
                          <span className="meta-value">
                            {model.training_date ? formatDate(model.training_date) : 'N/A'}
                          </span>
                        </div>
                        {model.feature_config_id && featureConfigs[model.feature_config_id] && (
                          <div className="meta-item" style={{ gridColumn: '1 / -1' }}>
                            <span className="meta-label">Feature Config:</span>
                            <span className="meta-value" style={{ fontWeight: '500', color: '#4CAF50' }}>
                              {featureConfigs[model.feature_config_id].name}
                            </span>
                            <span style={{ fontSize: '11px', color: '#787b86', marginLeft: '8px' }}>
                              ({featureConfigs[model.feature_config_id].transformations?.length || 0} features)
                            </span>
                          </div>
                        )}
                      </div>

                      <div className="model-metrics">
                        <h4>Performance Metrics</h4>
                        <div className="metrics-grid">
                          <div className="metric">
                            <span className="metric-label">Accuracy</span>
                            <span className="metric-value">
                              {formatMetric(model.accuracy)}
                            </span>
                          </div>
                          <div className="metric">
                            <span className="metric-label">Precision</span>
                            <span className="metric-value">
                              {formatMetric(model.precision_score)}
                            </span>
                          </div>
                          <div className="metric">
                            <span className="metric-label">Recall</span>
                            <span className="metric-value">
                              {formatMetric(model.recall)}
                            </span>
                          </div>
                          <div className="metric">
                            <span className="metric-label">F1 Score</span>
                            <span className="metric-value">
                              {formatMetric(model.f1_score)}
                            </span>
                          </div>
                        </div>
                      </div>

                      <div className="model-actions">
                        <label style={{ color: '#787b86', fontSize: '12px', marginBottom: '5px', display: 'block' }}>
                          Change Status:
                        </label>
                        <select
                          value={model.status}
                          onChange={(e) => changeModelStatus(model.model_id, e.target.value)}
                          style={{
                            background: '#1e222d',
                            border: '1px solid #2a2e39',
                            color: '#d1d4dc',
                            padding: '8px 12px',
                            borderRadius: '4px',
                            width: '100%',
                            cursor: 'pointer',
                            fontSize: '13px'
                          }}
                        >
                          <option value="active">Active</option>
                          <option value="production">Production</option>
                          <option value="staging">Staging</option>
                          <option value="archived">Archived</option>
                        </select>
                      </div>

                      {model.model_metadata && (
                        <div className="model-details">
                          <details>
                            <summary>Training Configuration & Feature Engineering</summary>
                            <div className="metadata-container">
                              {renderMetadataTable(model.model_metadata, model)}
                            </div>
                          </details>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="experiments-section">
              {experiments.length === 0 ? (
                <div className="empty-state">
                  <p>No experiments found</p>
                </div>
              ) : (
                <div>
                  <div className="experiments-list">
                    {experiments.map(exp => (
                      <div
                        key={exp.experiment_id}
                        className={`experiment-card ${selectedExperiment === exp.experiment_id ? 'selected' : ''}`}
                        onClick={() => fetchRuns(exp.experiment_id)}
                      >
                        <h3>{exp.name}</h3>
                        <div className="experiment-meta">
                          <span>ID: {exp.experiment_id}</span>
                          <span>Stage: {exp.lifecycle_stage}</span>
                        </div>
                      </div>
                    ))}
                  </div>

                  {selectedExperiment && runs.length > 0 && (
                    <div className="runs-section">
                      <h3>Training Runs</h3>
                      <div className="runs-list">
                        {runs.map(run => (
                          <div key={run.run_id} className="run-card">
                            <div className="run-header">
                              <div>
                                <h4>{run.tags?.['mlflow.runName'] || run.run_id}</h4>
                                <span className={`run-status ${run.status.toLowerCase()}`}>
                                  {run.status}
                                </span>
                              </div>
                              <div className="run-times">
                                <div>Started: {formatDate(run.start_time)}</div>
                                <div>Ended: {formatDate(run.end_time)}</div>
                              </div>
                            </div>

                            {run.metrics && (
                              <div className="run-metrics">
                                <h5>Metrics</h5>
                                <div className="metrics-grid">
                                  {Object.entries(run.metrics)
                                    .filter(([key]) => key.includes('test_'))
                                    .map(([key, value]) => (
                                      <div key={key} className="metric">
                                        <span className="metric-label">
                                          {key.replace('test_', '').replace('_', ' ')}
                                        </span>
                                        <span className="metric-value">
                                          {formatMetric(value)}
                                        </span>
                                      </div>
                                    ))}
                                </div>
                              </div>
                            )}

                            {run.params && (
                              <div className="run-params">
                                <details>
                                  <summary>Parameters ({Object.keys(run.params).length})</summary>
                                  <div className="params-grid">
                                    {Object.entries(run.params).map(([key, value]) => (
                                      <div key={key} className="param">
                                        <span className="param-label">{key}:</span>
                                        <span className="param-value">{value}</span>
                                      </div>
                                    ))}
                                  </div>
                                </details>
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Training Configuration Modal */}
      {showTrainingModal && (
        <div className="modal-overlay" onClick={() => setShowTrainingModal(false)}>
          <div className="modal-content training-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>Configure Model Training</h2>
              <button className="modal-close" onClick={() => setShowTrainingModal(false)}>×</button>
            </div>
            
            <form onSubmit={handleTrainingSubmit}>
              <div className="form-group">
                <label>Sensor ID</label>
                <input
                  type="text"
                  value={trainingConfig.sensor_id}
                  onChange={(e) => setTrainingConfig({...trainingConfig, sensor_id: e.target.value})}
                  placeholder="all_sensors or specific sensor ID"
                  required
                />
                <small>Use 'all_sensors' for multivariate model or specific sensor ID</small>
              </div>

              <div className="form-group">
                <label>Sensor Type</label>
                <select
                  value={trainingConfig.sensor_type}
                  onChange={(e) => setTrainingConfig({...trainingConfig, sensor_type: e.target.value})}
                  required
                >
                  <option value="multivariate">Multivariate</option>
                  <option value="temperature">Temperature</option>
                  <option value="pressure">Pressure</option>
                  <option value="vibration">Vibration</option>
                  <option value="flow">Flow</option>
                </select>
              </div>

              <div className="form-group">
                <label>Lookback Window (days)</label>
                <input
                  type="number"
                  min="1"
                  max="365"
                  value={trainingConfig.lookback_days}
                  onChange={(e) => setTrainingConfig({...trainingConfig, lookback_days: e.target.value})}
                  required
                />
                <small>Number of days of historical data to use for training</small>
              </div>

              <div className="form-group">
                <label>Contamination Rate</label>
                <input
                  type="number"
                  step="0.01"
                  min="0.01"
                  max="0.5"
                  value={trainingConfig.contamination}
                  onChange={(e) => setTrainingConfig({...trainingConfig, contamination: e.target.value})}
                  required
                />
                <small>Expected proportion of anomalies (0.01-0.5, typical: 0.1)</small>
              </div>

              <div className="form-group">
                <label>Number of Estimators</label>
                <input
                  type="number"
                  min="50"
                  max="500"
                  step="50"
                  value={trainingConfig.n_estimators}
                  onChange={(e) => setTrainingConfig({...trainingConfig, n_estimators: e.target.value})}
                  required
                />
                <small>Number of trees in the Isolation Forest (typical: 100)</small>
              </div>

              <div className="form-group">
                <label>Description (optional)</label>
                <textarea
                  value={trainingConfig.description}
                  onChange={(e) => setTrainingConfig({...trainingConfig, description: e.target.value})}
                  placeholder="Brief description of this training run..."
                  rows="3"
                />
              </div>

              <div className="form-actions">
                <button type="button" className="btn-secondary" onClick={() => setShowTrainingModal(false)}>
                  Cancel
                </button>
                <button type="submit" className="btn-primary">
                  Start Training
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

export default MLModels;
