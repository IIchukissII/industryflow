// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect } from 'react';
import Icon from '../components/Icon';
import './AlertHistory.css';
import authFetch from '../services/http';

// Severity → status token. Maps the five levels onto the platform's status palette so the page
// reads as one system with the rest of the app (no ad-hoc hex).
const SEVERITY = {
  critical: 'var(--crit)',
  high: 'var(--warn)',
  medium: 'var(--signal)',
  low: 'var(--live)',
  info: 'var(--faint)',
};
const sevColor = (s) => SEVERITY[s] || SEVERITY.info;

// Operator verdict vocabulary (ADR-0022): "was this alert real?" — distinct from acknowledge.
const VERDICTS = [
  { key: 'true_positive', label: 'Real', tone: 'ok' },
  { key: 'false_positive', label: 'False', tone: 'bad' },
  { key: 'unsure', label: 'Unsure', tone: 'muted' },
];

// A compact three-way label control per alert. The active verdict is highlighted, so it doubles
// as the verdict display — colour is never the only signal (each button is also worded).
function LabelControl({ alert, onLabel }) {
  const [busy, setBusy] = useState(false);
  const current = alert.label_verdict || null;

  const set = async (verdict) => {
    if (busy || verdict === current) return;
    setBusy(true);
    await onLabel(alert.alert_id, verdict);
    setBusy(false);
  };

  return (
    <div className="alh-label" role="group" aria-label="Label whether this alert was real">
      {VERDICTS.map(v => (
        <button
          key={v.key}
          type="button"
          className={`alh-verdict alh-verdict-${v.tone}${current === v.key ? ' active' : ''}`}
          onClick={() => set(v.key)}
          disabled={busy}
          aria-pressed={current === v.key}
          title={`Mark this alert as ${v.label.toLowerCase()}`}
        >
          {v.label}
        </button>
      ))}
    </div>
  );
}

function AlertHistory() {
  const [alerts, setAlerts] = useState([]);
  const [alertRules, setAlertRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedRule, setSelectedRule] = useState('all');
  const [filterSeverity, setFilterSeverity] = useState('all');
  const [filterType, setFilterType] = useState('all');
  const [metrics, setMetrics] = useState(null); // operator-label precision (ADR-0022)

  useEffect(() => {
    fetchAlerts();
    fetchAlertRules();
    fetchMetrics();
    const interval = setInterval(fetchAlerts, 10000);
    return () => clearInterval(interval);
  }, []);

  const fetchAlerts = async () => {
    try {
      const response = await authFetch('/api/alerts?limit=1000');
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
      const response = await authFetch('/api/alert-rules');
      if (!response.ok) throw new Error('Failed to fetch alert rules');
      const data = await response.json();
      setAlertRules(data || []);
    } catch (err) {
      console.error('Failed to fetch alert rules:', err);
    }
  };

  // Operator-label precision over the last 30 days (ADR-0022). Recall is intentionally absent.
  const fetchMetrics = async () => {
    try {
      const response = await authFetch('/api/alerts/label-metrics?days=30');
      if (response.ok) setMetrics(await response.json());
    } catch (err) {
      /* precision panel just stays empty if unavailable */
    }
  };

  // Record an operator's correctness verdict on an alert (ADR-0022). Optimistic: reflect the
  // verdict locally on success, then refresh precision.
  const labelAlert = async (alertId, verdict) => {
    try {
      const response = await authFetch(`/api/alerts/${alertId}/label`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ verdict }),
      });
      if (!response.ok) throw new Error('Failed to label alert');
      setAlerts(prev => prev.map(a => (a.alert_id === alertId ? { ...a, label_verdict: verdict } : a)));
      fetchMetrics();
    } catch (err) {
      console.error('Failed to label alert:', err);
    }
  };

  // Acknowledge = "seen" (distinct from the correctness label). Optimistic.
  const acknowledgeAlert = async (alertId) => {
    try {
      const response = await authFetch(`/api/alerts/${alertId}/acknowledge`, { method: 'PATCH' });
      if (!response.ok) throw new Error('Failed to acknowledge alert');
      setAlerts(prev => prev.map(a => (a.alert_id === alertId
        ? { ...a, acknowledged: true, acknowledged_at: new Date().toISOString() } : a)));
    } catch (err) {
      console.error('Failed to acknowledge alert:', err);
    }
  };

  const formatDate = (ts) => new Date(ts).toLocaleString();

  const filteredAlerts = alerts.filter(alert => {
    const severityMatch = filterSeverity === 'all' || alert.severity === filterSeverity;
    const typeMatch = filterType === 'all' || alert.detection_type === filterType;
    const ruleMatch = selectedRule === 'all' || alert.rule_id === selectedRule;
    return severityMatch && typeMatch && ruleMatch;
  });

  // Timeline: alerts per hour over the last 24h.
  const getHourlyAlertCounts = () => {
    const now = new Date();
    const hours = [];
    const counts = {};
    for (let i = 23; i >= 0; i--) {
      const hour = new Date(now.getTime() - i * 60 * 60 * 1000);
      const hourKey = hour.toLocaleTimeString([], { hour: '2-digit', hour12: false }) + ':00';
      hours.push(hourKey);
      counts[hourKey] = 0;
    }
    filteredAlerts.forEach(alert => {
      const hourKey = new Date(alert.triggered_at).toLocaleTimeString([], { hour: '2-digit', hour12: false }) + ':00';
      if (counts[hourKey] !== undefined) counts[hourKey]++;
    });
    return { hours, counts: hours.map(h => counts[h]) };
  };

  const getSeverityDistribution = () => {
    const distribution = { critical: 0, high: 0, medium: 0, low: 0, info: 0 };
    filteredAlerts.forEach(alert => {
      if (distribution[alert.severity] !== undefined) distribution[alert.severity]++;
    });
    return distribution;
  };

  const hourlyData = getHourlyAlertCounts();
  const severityDist = getSeverityDistribution();
  const maxCount = Math.max(...hourlyData.counts, 1);

  if (loading) return <div className="alh-state"><span className="sdot pending" /> Loading alerts…</div>;
  if (error) return <div className="alh-state"><Icon name="alert" size={20} color="var(--crit)" /> {error}</div>;

  return (
    <div className="alh-wrap">
      <div className="page-head">
        <div>
          <div className="eyebrow">Operations</div>
          <h1>Alert history</h1>
          <div className="sub">Fired alerts across your tenant — label them real/false to measure model precision.</div>
        </div>
        <button onClick={fetchAlerts} className="btn btn-secondary btn-sm">
          <Icon name="refresh" size={14} /> Refresh
        </button>
      </div>

      <div className="kpi-row alh-kpis">
        <div className="kpi">
          <div className="kpi-label">Total alerts</div>
          <div className="kpi-value">{filteredAlerts.length}</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--crit)' }}>
          <div className="kpi-label">Critical</div>
          <div className="kpi-value" style={{ color: 'var(--crit)' }}>{severityDist.critical}</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--warn)' }}>
          <div className="kpi-label">High priority</div>
          <div className="kpi-value" style={{ color: 'var(--warn)' }}>{severityDist.high}</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--signal)' }}>
          <div className="kpi-label">Unacknowledged</div>
          <div className="kpi-value">{filteredAlerts.filter(a => !a.acknowledged).length}</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--live)' }} title="Precision from operator labels over the last 30 days. Recall is not measurable from fired-alert labels (ADR-0022).">
          <div className="kpi-label">Precision (labelled)</div>
          <div className="kpi-value">
            {metrics?.overall?.precision != null ? `${Math.round(metrics.overall.precision * 100)}%` : '—'}
          </div>
          <div className="kpi-foot">
            {metrics?.overall?.labeled_total ? `${metrics.overall.labeled_total} labelled · 30d` : 'label alerts below to measure'}
          </div>
        </div>
      </div>

      <section className="panel alh-filters-panel">
        <div className="alh-filters">
          <label className="alh-field">
            <span className="alh-field-label">Alert rule</span>
            <select className="alh-select" value={selectedRule} onChange={(e) => setSelectedRule(e.target.value)}>
              <option value="all">All rules</option>
              {alertRules.map(rule => <option key={rule.rule_id} value={rule.rule_id}>{rule.name}</option>)}
            </select>
          </label>
          <label className="alh-field">
            <span className="alh-field-label">Severity</span>
            <select className="alh-select" value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)}>
              <option value="all">All severities</option>
              <option value="critical">Critical</option>
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
          </label>
          <label className="alh-field">
            <span className="alh-field-label">Detection type</span>
            <select className="alh-select" value={filterType} onChange={(e) => setFilterType(e.target.value)}>
              <option value="all">All types</option>
              <option value="ml">ML detection</option>
              <option value="threshold">Threshold</option>
              <option value="statistical">Statistical (drift)</option>
            </select>
          </label>
        </div>
      </section>

      <div className="alh-charts">
        <section className="panel alh-chart">
          <div className="panel-head"><h2>Alert timeline · last 24h</h2></div>
          <div className="alh-timeline" role="img" aria-label="Alerts per hour over the last 24 hours">
            {hourlyData.hours.map((hour, index) => {
              const count = hourlyData.counts[index];
              const barHeight = count > 0 ? Math.max((count / maxCount) * 160, 6) : 2;
              return (
                <div key={hour} className="alh-bar-col">
                  <div className="alh-bar-count">{count > 0 ? count : ''}</div>
                  <div
                    className={`alh-bar${count > 0 ? '' : ' empty'}`}
                    style={{ height: `${barHeight}px` }}
                    title={`${hour}: ${count} alert${count === 1 ? '' : 's'}`}
                  />
                  <div className="alh-bar-label">{index % 3 === 0 ? hour.slice(0, 2) + 'h' : ''}</div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="panel alh-chart">
          <div className="panel-head"><h2>By severity</h2></div>
          <div className="alh-sevdist">
            {Object.entries(severityDist).map(([severity, count]) => (
              <div className="alh-sev-row" key={severity} style={{ '--sev': sevColor(severity) }}>
                <div className="alh-sev-head">
                  <span className="alh-sev-name">{severity}</span>
                  <span className="alh-sev-count mono">{count}</span>
                </div>
                <div className="alh-sev-track">
                  <div className="alh-sev-fill" style={{ width: `${(count / (filteredAlerts.length || 1)) * 100}%` }} />
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="panel alh-list-panel">
        <div className="panel-head"><h2>Recent alerts <span className="alh-count">{filteredAlerts.length}</span></h2></div>
        <div className="alh-list">
          {filteredAlerts.length === 0 ? (
            <div className="alh-empty"><Icon name="bell" size={22} color="var(--faint)" /><p>No alerts match your filters.</p></div>
          ) : (
            filteredAlerts.slice(0, 30).map(alert => (
              <div key={alert.alert_id} className={`alh-alert${alert.acknowledged ? ' is-ack' : ''}`} style={{ '--sev': sevColor(alert.severity) }}>
                <div className="alh-alert-body">
                  <div className="alh-alert-msg">{alert.message}</div>
                  <div className="alh-alert-meta">
                    {alert.equipment_name && <span><Icon name="box" size={12} /> {alert.equipment_name}</span>}
                    {alert.sensor_name && <span><Icon name="sensor" size={12} /> {alert.sensor_name}</span>}
                    <span><Icon name="clock" size={12} /> {formatDate(alert.triggered_at)}</span>
                    {alert.detection_type === 'ml' && alert.anomaly_score != null && (
                      <span className="alh-score"><Icon name="cpu" size={12} /> {alert.anomaly_score.toFixed(3)}</span>
                    )}
                  </div>
                </div>
                <div className="alh-alert-right">
                  <span className="badge alh-sev-badge">{alert.severity}</span>
                  <LabelControl alert={alert} onLabel={labelAlert} />
                  {alert.acknowledged ? (
                    <span className="alh-ack-done" title={alert.acknowledged_at ? `Acknowledged ${formatDate(alert.acknowledged_at)}` : 'Acknowledged'}>
                      <Icon name="check" size={12} /> Ack
                    </span>
                  ) : (
                    <button className="alh-ack-btn" onClick={() => acknowledgeAlert(alert.alert_id)}>Acknowledge</button>
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  );
}

export default AlertHistory;
