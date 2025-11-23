import React, { useState, useEffect } from 'react';
import './AlertHistory.css';
import Header from '../components/Header';

function AlertHistory() {
  const [alerts, setAlerts] = useState([]);
  const [alertRules, setAlertRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [user, setUser] = useState(null);
  const [connected, setConnected] = useState(false);
  const [selectedRule, setSelectedRule] = useState('all');
  const [filterSeverity, setFilterSeverity] = useState('all');
  const [filterType, setFilterType] = useState('all');

  useEffect(() => {
    // Get user from localStorage
    const savedUser = localStorage.getItem('user');
    if (savedUser) {
      setUser(JSON.parse(savedUser));
    }

    // Check API connection
    fetch('http://localhost:8000/health')
      .then(res => res.json())
      .then(() => setConnected(true))
      .catch(() => setConnected(false));

    fetchAlerts();
    fetchAlertRules();
    // Refresh every 10 seconds
    const interval = setInterval(fetchAlerts, 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchAlerts = async () => {
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch('http://localhost:8000/api/alerts', {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (!response.ok) throw new Error('Failed to fetch alerts');

      const data = await response.json();
      setAlerts(data || []);
      setLoading(false);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  };

  const fetchAlertRules = async () => {
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch('http://localhost:8000/api/alert-rules', {
        headers: {
          'Authorization': `Bearer ${token}`
        }
      });

      if (!response.ok) throw new Error('Failed to fetch alert rules');

      const data = await response.json();
      setAlertRules(data || []);
    } catch (err) {
      console.error('Failed to fetch alert rules:', err);
    }
  };

  const exportToCSV = () => {
    const headers = ['Time', 'Detection Type', 'Severity', 'Equipment', 'Sensor', 'Message', 'Actual Value', 'Threshold', 'Status'];
    const csvData = filteredAlerts.map(alert => [
      new Date(alert.triggered_at).toISOString(),
      alert.detection_type,
      alert.severity,
      alert.equipment_name || alert.equipment_id || '',
      alert.sensor_name || alert.sensor_id || '',
      alert.message,
      alert.actual_value || '',
      alert.threshold_value || '',
      alert.acknowledged ? 'Acknowledged' : 'New'
    ]);

    const csvContent = [
      headers.join(','),
      ...csvData.map(row => row.map(cell => `"${cell}"`).join(','))
    ].join('\n');

    const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' });
    const link = document.createElement('a');
    const url = URL.createObjectURL(blob);
    link.setAttribute('href', url);
    link.setAttribute('download', `alerts_${new Date().toISOString().split('T')[0]}.csv`);
    link.style.visibility = 'hidden';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  const getSeverityColor = (severity) => {
    const colors = {
      critical: '#dc3545',
      high: '#fd7e14',
      medium: '#ffc107',
      low: '#17a2b8',
      info: '#6c757d'
    };
    return colors[severity] || colors.info;
  };

  const formatDate = (timestamp) => {
    return new Date(timestamp).toLocaleString();
  };

  const getRelativeTime = (timestamp) => {
    const now = new Date();
    const then = new Date(timestamp);
    const diffMs = now - then;
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    return `${diffDays}d ago`;
  };

  // Filter alerts
  const filteredAlerts = alerts.filter(alert => {
    const severityMatch = filterSeverity === 'all' || alert.severity === filterSeverity;
    const typeMatch = filterType === 'all' || alert.detection_type === filterType;
    const ruleMatch = selectedRule === 'all' || alert.rule_id === selectedRule;
    return severityMatch && typeMatch && ruleMatch;
  });

  const renderCardView = () => (
    <div className="alerts-cards">
      {filteredAlerts.length === 0 ? (
        <div className="no-alerts-card">
          <div className="no-alerts-icon">📭</div>
          <h3>No alerts match your filters</h3>
          <p>Try adjusting the filters or search term above</p>
        </div>
      ) : (
        filteredAlerts.map((alert) => (
          <div key={alert.alert_id} className={`alert-card ${alert.severity} ${!alert.acknowledged ? 'new' : ''}`}>
            <div className="alert-card-header">
              <div className="alert-badges">
                <span className={`detection-badge ${alert.detection_type}`}>
                  {alert.detection_type === 'ml' && '🤖 ML'}
                  {alert.detection_type === 'threshold' && '📊 Threshold'}
                  {alert.detection_type === 'statistical' && '📈 Statistical'}
                </span>
                <span
                  className="severity-badge"
                  style={{ backgroundColor: getSeverityColor(alert.severity) }}
                >
                  {alert.severity}
                </span>
              </div>
              <div className="alert-time">
                <span className="relative-time">{getRelativeTime(alert.triggered_at)}</span>
                <span className="absolute-time">{formatDate(alert.triggered_at)}</span>
              </div>
            </div>

            <div className="alert-card-body">
              <div className="alert-message">{alert.message}</div>

              <div className="alert-details">
                {alert.equipment_id && (
                  <div className="detail-item">
                    <span className="detail-label">Equipment:</span>
                    <span className="detail-value">{alert.equipment_name || alert.equipment_id.slice(0, 13) + '...'}</span>
                  </div>
                )}
                {alert.sensor_id && (
                  <div className="detail-item">
                    <span className="detail-label">Sensor:</span>
                    <span className="detail-value">{alert.sensor_name || alert.sensor_id.slice(0, 13) + '...'}</span>
                  </div>
                )}

                {alert.detection_type === 'threshold' && alert.actual_value && (
                  <div className="detail-item">
                    <span className="detail-label">Value:</span>
                    <span className="detail-value value-critical">
                      {typeof alert.actual_value === 'number' ? alert.actual_value.toFixed(2) : alert.actual_value}
                    </span>
                    {alert.threshold_value && (
                      <span className="detail-meta"> (threshold: {alert.threshold_value.toFixed(2)})</span>
                    )}
                  </div>
                )}

                {alert.detection_type === 'ml' && (
                  <div className="detail-item">
                    <span className="detail-label">Anomaly Score:</span>
                    <span className="detail-value value-ml">
                      {alert.anomaly_score ? alert.anomaly_score.toFixed(3) : 'N/A'}
                    </span>
                  </div>
                )}
              </div>
            </div>

            <div className="alert-card-footer">
              {alert.acknowledged ? (
                <span className="status-badge ack">✓ Acknowledged</span>
              ) : (
                <span className="status-badge pending">⚠ New Alert</span>
              )}
            </div>
          </div>
        ))
      )}
    </div>
  );

  const renderTableView = () => (
    <div className="alerts-table-container">
      <table className="alerts-table">
        <thead>
          <tr>
            <th>Time</th>
            <th>Detection Type</th>
            <th>Severity</th>
            <th>Equipment/Sensor</th>
            <th>Message</th>
            <th>Value</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          {filteredAlerts.length === 0 ? (
            <tr>
              <td colSpan="7" className="no-data">
                <div style={{padding: '20px'}}>
                  <h3>No alerts found</h3>
                  <p style={{color: '#95a5a6', marginTop: '10px'}}>
                    Alerts will appear here when threshold, ML, or statistical anomalies are detected.
                  </p>
                </div>
              </td>
            </tr>
          ) : (
            filteredAlerts.map((alert) => (
              <tr key={alert.alert_id}>
                <td className="time-cell">{formatDate(alert.triggered_at)}</td>
                <td>
                  <span className={`detection-badge ${alert.detection_type}`}>
                    {alert.detection_type === 'ml' && '🤖 ML'}
                    {alert.detection_type === 'threshold' && '📊 Threshold'}
                    {alert.detection_type === 'statistical' && '📈 Statistical'}
                  </span>
                </td>
                <td>
                  <span
                    className="severity-badge"
                    style={{ backgroundColor: getSeverityColor(alert.severity) }}
                  >
                    {alert.severity}
                  </span>
                </td>
                <td className="equipment-cell">
                  {alert.equipment_id ? (
                    <div>
                      <div style={{fontWeight: 600}}>Equipment</div>
                      <div style={{fontSize: '11px', color: '#7f8c8d'}}>
                        {alert.equipment_name || alert.equipment_id.slice(0, 8) + '...'}
                      </div>
                    </div>
                  ) : (
                    <div>
                      <div style={{fontWeight: 600}}>Sensor</div>
                      <div style={{fontSize: '11px', color: '#7f8c8d'}}>
                        {alert.sensor_name || alert.sensor_id?.slice(0, 8) + '...'}
                      </div>
                    </div>
                  )}
                </td>
                <td className="message">{alert.message}</td>
                <td className="score-cell">
                  {alert.detection_type === 'threshold' && alert.actual_value && (
                    <div>
                      <span style={{color: '#e74c3c', fontWeight: 'bold'}}>
                        {typeof alert.actual_value === 'number' ? alert.actual_value.toFixed(2) : alert.actual_value}
                      </span>
                      {alert.threshold_value && (
                        <div style={{fontSize: '11px', color: '#95a5a6'}}>
                          Threshold: {alert.threshold_value.toFixed(2)}
                        </div>
                      )}
                    </div>
                  )}
                  {alert.detection_type === 'ml' && (
                    <div>
                      <span style={{color: '#9b59b6', fontWeight: 'bold'}}>
                        {alert.anomaly_score ? alert.anomaly_score.toFixed(3) : 'N/A'}
                      </span>
                      <div style={{fontSize: '11px', color: '#95a5a6'}}>
                        ML Score
                      </div>
                    </div>
                  )}
                </td>
                <td>
                  {alert.acknowledged ? (
                    <span className="status-badge ack">✓ Acknowledged</span>
                  ) : (
                    <span className="status-badge pending">⚠ New</span>
                  )}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );

  if (loading) return (
    <div className="App">
      <Header user={user} connected={connected} />
      <div className="loading">Loading alerts...</div>
    </div>
  );

  if (error) return (
    <div className="App">
      <Header user={user} connected={connected} />
      <div className="error">Error: {error}</div>
    </div>
  );

  return (
    <div className="App">
      <Header user={user} connected={connected} />

      <main className="App-main">
        <div className="alert-history">
          <div className="page-header">
            <h2>Alert History</h2>
            <div className="header-actions">
              <button onClick={exportToCSV} className="export-btn" title="Export to CSV">
                📥 Export CSV
              </button>
              <button onClick={fetchAlerts} className="refresh-btn" title="Refresh alerts">
                🔄 Refresh
              </button>
            </div>
          </div>

          <div className="alerts-stats">
            <div className="stat-card">
              <div className="stat-icon">📊</div>
              <h3>Total Alerts</h3>
              <div className="stat-value">{filteredAlerts.length}</div>
              <div className="stat-meta">of {alerts.length} total</div>
            </div>
            <div className="stat-card critical">
              <div className="stat-icon">🚨</div>
              <h3>Critical</h3>
              <div className="stat-value">
                {alerts.filter(a => a.severity === 'critical').length}
              </div>
            </div>
            <div className="stat-card high">
              <div className="stat-icon">⚠️</div>
              <h3>High Priority</h3>
              <div className="stat-value">
                {alerts.filter(a => a.severity === 'high').length}
              </div>
            </div>
            <div className="stat-card">
              <div className="stat-icon">🔔</div>
              <h3>Unacknowledged</h3>
              <div className="stat-value">
                {alerts.filter(a => !a.acknowledged).length}
              </div>
            </div>
          </div>

          <div className="controls-bar">
            <div className="filters">
              <div className="search-box">
                <span className="search-icon">🔍</span>
                <input
                  type="text"
                  placeholder="Search alerts..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="search-input"
                />
              </div>

              <select
                value={filterSeverity}
                onChange={(e) => setFilterSeverity(e.target.value)}
                className="filter-select"
              >
                <option value="all">All Severities</option>
                <option value="critical">Critical</option>
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>

              <select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value)}
                className="filter-select"
              >
                <option value="all">All Types</option>
                <option value="ml">ML Detection</option>
                <option value="threshold">Threshold</option>
                <option value="statistical">Statistical</option>
              </select>
            </div>

            <div className="view-toggle">
              <button
                className={`view-btn ${viewMode === 'cards' ? 'active' : ''}`}
                onClick={() => setViewMode('cards')}
                title="Card view"
              >
                📇 Cards
              </button>
              <button
                className={`view-btn ${viewMode === 'table' ? 'active' : ''}`}
                onClick={() => setViewMode('table')}
                title="Table view"
              >
                📋 Table
              </button>
            </div>
          </div>

          {viewMode === 'cards' ? renderCardView() : renderTableView()}
        </div>
      </main>
    </div>
  );
}

export default AlertHistory;
