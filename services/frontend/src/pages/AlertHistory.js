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
    const savedUser = localStorage.getItem('user');
    if (savedUser) {
      setUser(JSON.parse(savedUser));
    }

    fetch('http://localhost:8000/health')
      .then(res => res.json())
      .then(() => setConnected(true))
      .catch(() => setConnected(false));

    fetchAlerts();
    fetchAlertRules();
    const interval = setInterval(fetchAlerts, 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchAlerts = async () => {
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch('http://localhost:8000/api/alerts?limit=1000', {
        headers: { 'Authorization': `Bearer ${token}` }
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
        headers: { 'Authorization': `Bearer ${token}` }
      });
      if (!response.ok) throw new Error('Failed to fetch alert rules');
      const data = await response.json();
      setAlertRules(data || []);
    } catch (err) {
      console.error('Failed to fetch alert rules:', err);
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

  // Filter alerts
  const filteredAlerts = alerts.filter(alert => {
    const severityMatch = filterSeverity === 'all' || alert.severity === filterSeverity;
    const typeMatch = filterType === 'all' || alert.detection_type === filterType;
    const ruleMatch = selectedRule === 'all' || alert.rule_id === selectedRule;
    return severityMatch && typeMatch && ruleMatch;
  });

  // Prepare chart data - Group alerts by hour
  const getHourlyAlertCounts = () => {
    const now = new Date();
    const hours = [];
    const counts = {};

    // Last 24 hours
    for (let i = 23; i >= 0; i--) {
      const hour = new Date(now.getTime() - i * 60 * 60 * 1000);
      const hourKey = hour.toLocaleTimeString([], { hour: '2-digit', hour12: false }) + ':00';
      hours.push(hourKey);
      counts[hourKey] = 0;
    }

    filteredAlerts.forEach(alert => {
      const alertDate = new Date(alert.triggered_at);
      const hourKey = alertDate.toLocaleTimeString([], { hour: '2-digit', hour12: false }) + ':00';
      if (counts[hourKey] !== undefined) {
        counts[hourKey]++;
      }
    });

    return { hours, counts: hours.map(h => counts[h]) };
  };

  // Severity distribution
  const getSeverityDistribution = () => {
    const distribution = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
    filteredAlerts.forEach(alert => {
      if (distribution[alert.severity] !== undefined) {
        distribution[alert.severity]++;
      }
    });
    return distribution;
  };

  const hourlyData = getHourlyAlertCounts();
  const severityDist = getSeverityDistribution();
  const maxCount = Math.max(...hourlyData.counts, 1);

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
        <div style={{ maxWidth: '1400px', margin: '0 auto', padding: '20px' }}>
          {/* Header */}
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
            <h2 style={{ margin: 0 }}>Alert History</h2>
            <button onClick={fetchAlerts} style={{
              background: '#2962ff',
              color: 'white',
              border: 'none',
              padding: '10px 20px',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '14px'
            }}>
              🔄 Refresh
            </button>
          </div>

          {/* Stats Cards */}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '15px', marginBottom: '20px' }}>
            <div className="card" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '32px', marginBottom: '10px' }}>📊</div>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#2962ff' }}>{filteredAlerts.length}</div>
              <div style={{ color: '#787b86', fontSize: '13px' }}>Total Alerts</div>
            </div>
            <div className="card" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '32px', marginBottom: '10px' }}>🚨</div>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#dc3545' }}>{severityDist.critical}</div>
              <div style={{ color: '#787b86', fontSize: '13px' }}>Critical</div>
            </div>
            <div className="card" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '32px', marginBottom: '10px' }}>⚠️</div>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#fd7e14' }}>{severityDist.high}</div>
              <div style={{ color: '#787b86', fontSize: '13px' }}>High Priority</div>
            </div>
            <div className="card" style={{ textAlign: 'center' }}>
              <div style={{ fontSize: '32px', marginBottom: '10px' }}>🔔</div>
              <div style={{ fontSize: '24px', fontWeight: 'bold', color: '#2962ff' }}>
                {filteredAlerts.filter(a => !a.acknowledged).length}
              </div>
              <div style={{ color: '#787b86', fontSize: '13px' }}>Unacknowledged</div>
            </div>
          </div>

          {/* Filters */}
          <div className="card" style={{ marginBottom: '20px' }}>
            <h3>Filters</h3>
            <div style={{ display: 'flex', gap: '15px', marginTop: '15px' }}>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', color: '#787b86', marginBottom: '5px', fontSize: '13px' }}>Alert Rule</label>
                <select
                  value={selectedRule}
                  onChange={(e) => setSelectedRule(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: '#1a1e2e',
                    border: '1px solid #2a2e39',
                    borderRadius: '4px',
                    color: '#d1d4dc',
                    fontSize: '14px'
                  }}
                >
                  <option value="all">All Rules</option>
                  {alertRules.map(rule => (
                    <option key={rule.rule_id} value={rule.rule_id}>{rule.name}</option>
                  ))}
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', color: '#787b86', marginBottom: '5px', fontSize: '13px' }}>Severity</label>
                <select
                  value={filterSeverity}
                  onChange={(e) => setFilterSeverity(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: '#1a1e2e',
                    border: '1px solid #2a2e39',
                    borderRadius: '4px',
                    color: '#d1d4dc',
                    fontSize: '14px'
                  }}
                >
                  <option value="all">All Severities</option>
                  <option value="critical">Critical</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </select>
              </div>
              <div style={{ flex: 1 }}>
                <label style={{ display: 'block', color: '#787b86', marginBottom: '5px', fontSize: '13px' }}>Detection Type</label>
                <select
                  value={filterType}
                  onChange={(e) => setFilterType(e.target.value)}
                  style={{
                    width: '100%',
                    padding: '8px',
                    background: '#1a1e2e',
                    border: '1px solid #2a2e39',
                    borderRadius: '4px',
                    color: '#d1d4dc',
                    fontSize: '14px'
                  }}
                >
                  <option value="all">All Types</option>
                  <option value="ml">ML Detection</option>
                  <option value="threshold">Threshold</option>
                  <option value="statistical">Statistical</option>
                </select>
              </div>
            </div>
          </div>

          {/* Charts Row */}
          <div style={{ display: 'grid', gridTemplateColumns: '2fr 1fr', gap: '20px', marginBottom: '20px' }}>
            {/* Timeline Chart */}
            <div className="card">
              <h3>Alert Timeline (Last 24 Hours)</h3>
              <div style={{ marginTop: '20px', height: '200px', display: 'flex', alignItems: 'flex-end', gap: '3px' }}>
                {hourlyData.hours.map((hour, index) => {
                  const count = hourlyData.counts[index];
                  const barHeight = count > 0 ? Math.max((count / maxCount) * 180, 8) : 2;
                  return (
                    <div key={hour} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center' }}>
                      {/* Count badge - fixed height */}
                      <div style={{ height: '24px', display: 'flex', alignItems: 'flex-end', justifyContent: 'center', marginBottom: '4px' }}>
                        {count > 0 && (
                          <div style={{
                            fontSize: '11px',
                            color: '#2962ff',
                            fontWeight: 'bold',
                            padding: '2px 4px',
                            background: '#0a0e27',
                            borderRadius: '3px'
                          }}>{count}</div>
                        )}
                      </div>
                      {/* Bar */}
                      <div
                        title={`${hour}: ${count} alerts`}
                        style={{
                          width: '100%',
                          height: `${barHeight}px`,
                          background: count > 0 ? '#2962ff' : '#2a2e39',
                          borderRadius: '3px 3px 0 0',
                          transition: 'all 0.3s',
                          cursor: 'pointer',
                          boxShadow: count > 0 ? '0 0 10px rgba(41, 98, 255, 0.3)' : 'none'
                        }}
                        onMouseOver={(e) => {
                          if (count > 0) {
                            e.target.style.background = '#4285f4';
                            e.target.style.boxShadow = '0 0 15px rgba(41, 98, 255, 0.5)';
                          }
                        }}
                        onMouseOut={(e) => {
                          if (count > 0) {
                            e.target.style.background = '#2962ff';
                            e.target.style.boxShadow = '0 0 10px rgba(41, 98, 255, 0.3)';
                          }
                        }}
                      />
                      {/* Hour label - fixed height for all bars */}
                      <div style={{ height: '18px', display: 'flex', alignItems: 'center', justifyContent: 'center', marginTop: '4px' }}>
                        {index % 3 === 0 && (
                          <div style={{ fontSize: '10px', color: '#787b86' }}>
                            {hour.slice(0, 2)}h
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>

            {/* Severity Distribution */}
            <div className="card">
              <h3>Severity Distribution</h3>
              <div style={{ marginTop: '20px' }}>
                {Object.entries(severityDist).map(([severity, count]) => (
                  <div key={severity} style={{ marginBottom: '12px' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: '4px' }}>
                      <span style={{ fontSize: '13px', textTransform: 'capitalize' }}>{severity}</span>
                      <span style={{ fontSize: '13px', fontWeight: 'bold', color: getSeverityColor(severity) }}>{count}</span>
                    </div>
                    <div style={{ width: '100%', height: '8px', background: '#1a1e2e', borderRadius: '4px', overflow: 'hidden' }}>
                      <div style={{
                        width: `${(count / (filteredAlerts.length || 1)) * 100}%`,
                        height: '100%',
                        background: getSeverityColor(severity),
                        transition: 'width 0.3s'
                      }} />
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>

          {/* Recent Alerts List */}
          <div className="card">
            <h3>Recent Alerts ({filteredAlerts.length})</h3>
            <div style={{ marginTop: '15px', maxHeight: '400px', overflowY: 'auto' }}>
              {filteredAlerts.length === 0 ? (
                <div style={{ textAlign: 'center', color: '#787b86', padding: '40px' }}>
                  No alerts found matching your filters
                </div>
              ) : (
                filteredAlerts.slice(0, 20).map(alert => (
                  <div key={alert.alert_id} style={{
                    padding: '12px',
                    marginBottom: '8px',
                    background: '#1a1e2e',
                    borderLeft: `4px solid ${getSeverityColor(alert.severity)}`,
                    borderRadius: '4px',
                    display: 'flex',
                    justifyContent: 'space-between',
                    alignItems: 'center'
                  }}>
                    <div style={{ flex: 1 }}>
                      <div style={{ fontSize: '14px', fontWeight: '500', marginBottom: '4px' }}>
                        {alert.message}
                      </div>
                      <div style={{ fontSize: '12px', color: '#787b86' }}>
                        {alert.equipment_name && <span>📍 {alert.equipment_name}</span>}
                        {alert.sensor_name && <span style={{ marginLeft: '10px' }}>🔌 {alert.sensor_name}</span>}
                        <span style={{ marginLeft: '10px' }}>🕐 {formatDate(alert.triggered_at)}</span>
                      </div>
                    </div>
                    <div style={{ display: 'flex', gap: '10px', alignItems: 'center' }}>
                      <span style={{
                        padding: '4px 8px',
                        background: getSeverityColor(alert.severity),
                        color: 'white',
                        borderRadius: '3px',
                        fontSize: '11px',
                        fontWeight: 'bold',
                        textTransform: 'uppercase'
                      }}>
                        {alert.severity}
                      </span>
                      {alert.detection_type === 'ml' && (
                        <span style={{ fontSize: '11px', color: '#9b59b6' }}>
                          🤖 Score: {alert.anomaly_score?.toFixed(3)}
                        </span>
                      )}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

export default AlertHistory;
