import React, { useState, useEffect } from 'react';
import './AlertHistory.css';
import Header from '../components/Header';

function AlertHistory() {
  const [alerts, setAlerts] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [user, setUser] = useState(null);
  const [connected, setConnected] = useState(false);

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
            <button onClick={fetchAlerts} className="refresh-btn">
              🔄 Refresh
            </button>
          </div>

          <div className="alerts-stats">
            <div className="stat-card">
              <h3>Total Alerts</h3>
              <div className="stat-value">{alerts.length}</div>
            </div>
            <div className="stat-card critical">
              <h3>Critical</h3>
              <div className="stat-value">
                {alerts.filter(a => a.severity === 'critical').length}
              </div>
            </div>
            <div className="stat-card high">
              <h3>High</h3>
              <div className="stat-value">
                {alerts.filter(a => a.severity === 'high').length}
              </div>
            </div>
            <div className="stat-card">
              <h3>Unacknowledged</h3>
              <div className="stat-value">
                {alerts.filter(a => !a.acknowledged).length}
              </div>
            </div>
          </div>

          <div className="alerts-table-container">
            <table className="alerts-table">
              <thead>
                <tr>
                  <th>Time</th>
                  <th>Severity</th>
                  <th>Equipment</th>
                  <th>Message</th>
                  <th>Score</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {alerts.length === 0 ? (
                  <tr>
                    <td colSpan="6" className="no-data">No alerts found</td>
                  </tr>
                ) : (
                  alerts.map((alert) => (
                    <tr key={alert.alert_id}>
                      <td className="time-cell">{formatDate(alert.triggered_at)}</td>
                      <td>
                        <span 
                          className="severity-badge"
                          style={{ backgroundColor: getSeverityColor(alert.severity) }}
                        >
                          {alert.severity}
                        </span>
                      </td>
                      <td className="equipment-cell">{alert.equipment_id || alert.sensor_id}</td>
                      <td className="message">{alert.message}</td>
                      <td className="score-cell">
                        {alert.anomaly_score ? alert.anomaly_score.toFixed(3) : 'N/A'}
                      </td>
                      <td>
                        {alert.acknowledged ? (
                          <span className="status-badge ack">✓ Ack</span>
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
        </div>
      </main>
    </div>
  );
}

export default AlertHistory;
