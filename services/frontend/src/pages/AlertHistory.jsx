// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import Icon from '../components/Icon';
import './AlertHistory.css';
import authFetch from '../services/http';
import { canWrite, READ_ONLY_HINT } from '../utils/roles';
import { alertsReading } from '../utils/reading';

// Severity → status token: the alchemical stages for the two that need action, quiet greys below.
const SEVERITY = {
  critical: 'var(--crit)',
  high: 'var(--warn)',
  medium: '#b9bfcc',
  low: 'var(--muted)',
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
function LabelControl({ alert, onLabel, readOnly }) {
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
          disabled={busy || readOnly}
          aria-pressed={current === v.key}
          title={readOnly ? READ_ONLY_HINT : `Mark this alert as ${v.label.toLowerCase()}`}
        >
          {v.label}
        </button>
      ))}
    </div>
  );
}

// A tiny precision-over-time sparkline for the KPI. Plots only buckets with a defined precision
// (a bucket with no decisive labels is a gap, not a zero); the y-axis is fixed 0..1 so the line's
// height is comparable across renders. Returns null when there's not enough of a trend to draw.
function PrecisionSparkline({ series }) {
  const pts = (series || []).filter((b) => b.precision != null).map((b) => b.precision);
  if (pts.length < 2) return null;
  const w = 132, h = 26, pad = 2;
  const step = (w - pad * 2) / (pts.length - 1);
  const y = (v) => h - pad - v * (h - pad * 2);
  const d = pts.map((v, i) => `${i === 0 ? 'M' : 'L'}${(pad + i * step).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const last = pts[pts.length - 1];
  return (
    <svg className="alh-spark" width={w} height={h} viewBox={`0 0 ${w} ${h}`} role="img"
      aria-label={`Precision trend, latest ${Math.round(last * 100)} percent`}>
      <path d={d} fill="none" stroke="var(--live)" strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={(pad + (pts.length - 1) * step).toFixed(1)} cy={y(last).toFixed(1)} r="2" fill="var(--live)" />
    </svg>
  );
}

function AlertHistory() {
  // Acknowledging and labelling change tenant data: engineer or admin only (observer is read-only).
  const [writable] = useState(() => canWrite());
  const [alerts, setAlerts] = useState([]);
  const [alertRules, setAlertRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selectedRule, setSelectedRule] = useState('all');
  const [filterSeverity, setFilterSeverity] = useState('all');
  const [filterType, setFilterType] = useState('all');
  const [searchParams, setSearchParams] = useSearchParams();
  const sensorFilter = searchParams.get('sensor'); // set when arriving from a Convergence node
  const [metrics, setMetrics] = useState(null); // operator-label precision (ADR-0022)

  const fetchAlerts = useCallback(async () => {
    try {
      const response = await authFetch('/api/alerts?limit=500');
      if (!response.ok) throw new Error('Failed to fetch alerts');
      const data = await response.json();
      setAlerts(data || []);
      setLoading(false);
    } catch (err) {
      setError(err.message);
      setLoading(false);
    }
  }, []);

  const fetchAlertRules = useCallback(async () => {
    try {
      const response = await authFetch('/api/alert-rules');
      if (!response.ok) throw new Error('Failed to fetch alert rules');
      const data = await response.json();
      setAlertRules(data || []);
    } catch (err) {
      console.error('Failed to fetch alert rules:', err);
    }
  }, []);

  // Operator-label precision over the last 30 days (ADR-0022). Recall is intentionally absent.
  const fetchMetrics = useCallback(async () => {
    try {
      const response = await authFetch('/api/alerts/label-metrics?days=30');
      if (response.ok) setMetrics(await response.json());
    } catch {
      /* precision panel just stays empty if unavailable */
    }
  }, []);

  // Load on mount, then poll alerts. The initial loads run from an inner async function so the
  // effect body itself starts no synchronous setState; the interval fetch is already deferred.
  useEffect(() => {
    (async () => { await Promise.all([fetchAlerts(), fetchAlertRules(), fetchMetrics()]); })();
    const interval = setInterval(fetchAlerts, 20000);
    return () => clearInterval(interval);
  }, [fetchAlerts, fetchAlertRules, fetchMetrics]);

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

  // Today: the time alone. Earlier: a short date and the time.
  const formatDate = (ts) => {
    const d = new Date(ts);
    const today = new Date().toDateString() === d.toDateString();
    return today
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  };

  const filteredAlerts = alerts.filter(alert => {
    const severityMatch = filterSeverity === 'all' || alert.severity === filterSeverity;
    const typeMatch = filterType === 'all' || alert.detection_type === filterType;
    const ruleMatch = selectedRule === 'all' || alert.rule_id === selectedRule;
    const sensorMatch = !sensorFilter || alert.sensor_id === sensorFilter;
    return severityMatch && typeMatch && ruleMatch && sensorMatch;
  });
  // Name for the sensor chip when arriving from a Convergence node (falls back to the id).
  const sensorFilterName = sensorFilter
    ? (alerts.find(a => a.sensor_id === sensorFilter)?.sensor_name || `${sensorFilter.slice(0, 8)}…`)
    : null;

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

  const hourlyData = getHourlyAlertCounts();
  const maxCount = Math.max(...hourlyData.counts, 1);
  const open = filteredAlerts.filter((a) => !a.acknowledged);
  const earlier = filteredAlerts.filter((a) => a.acknowledged);
  const lastDayTotal = hourlyData.counts.reduce((n, c) => n + c, 0);
  const precision = metrics?.overall?.precision;
  const labelled = metrics?.overall?.labeled_total || 0;

  if (loading) return <div className="alh-state"><span className="sdot pending" /> Loading alerts</div>;
  if (error) return <div className="alh-state"><Icon name="alert" size={20} color="var(--crit)" /> {error}</div>;

  const row = (alert) => (
    <li key={alert.alert_id} className={`alh-alert${alert.acknowledged ? ' is-ack' : ' is-open'}`} style={{ '--sev': sevColor(alert.severity) }}>
      <span className="alh-mark" aria-hidden="true" />
      <div className="alh-alert-body">
        <div className="alh-alert-msg">{alert.message}</div>
        <div className="alh-alert-meta">
          {[alert.equipment_name, alert.sensor_name, alert.rule_name, alert.severity].filter(Boolean).join(', ')}
          {alert.triggered_at && <>{' '}<span className="mono">{formatDate(alert.triggered_at)}</span></>}
          {alert.detection_type === 'ml' && alert.anomaly_score != null && (
            <>, anomaly score <span className="mono">{alert.anomaly_score.toFixed(3)}</span></>
          )}
        </div>
      </div>
      <div className="alh-alert-right">
        {!alert.acknowledged && writable && (
          <button type="button" className="btn btn-primary" onClick={() => acknowledgeAlert(alert.alert_id)}>Acknowledge</button>
        )}
        {alert.acknowledged && (
          <span className="alh-ack-done">
            Acknowledged{alert.acknowledged_at ? <> <span className="mono">{formatDate(alert.acknowledged_at)}</span></> : ''}
          </span>
        )}
        <LabelControl alert={alert} onLabel={labelAlert} readOnly={!writable} />
      </div>
    </li>
  );

  return (
    <div className="alh">
      <div className="alh-head">
        <p className="reading">{alertsReading({ alerts })}</p>
        <div className="alh-actions">
          <a href="/alert-rules">Manage alert rules</a>
          <button type="button" onClick={fetchAlerts} className="btn btn-sm">
            <Icon name="refresh" size={14} /> Refresh
          </button>
        </div>
      </div>

      <section className="alh-day" aria-labelledby="alh-day-h">
        <div className="alh-day-head">
          <h2 id="alh-day-h">Last 24 hours</h2>
          <div className="alh-summary">
            <p>
              {lastDayTotal} fired, {open.length} open.{' '}
              {precision != null
                ? <>Labels put precision at <span className="mono">{Math.round(precision * 100)}%</span> over 30 days ({labelled} labelled).</>
                : 'Label alerts as real or false to measure precision.'}
            </p>
            <PrecisionSparkline series={metrics?.series} />
          </div>
        </div>
        <div className="alh-timeline" role="img" aria-label={`Alerts per hour over the last 24 hours: ${lastDayTotal} in total`}>
          {hourlyData.hours.map((hour, index) => {
            const count = hourlyData.counts[index];
            const barHeight = count > 0 ? Math.max((count / maxCount) * 56, 6) : 2;
            return (
              <div key={hour} className="alh-bar-col" title={`${hour}: ${count} alert${count === 1 ? '' : 's'}`}>
                <div className={`alh-bar${count > 0 ? '' : ' empty'}`} style={{ height: `${barHeight}px` }} />
                <div className="alh-bar-label">{index % 6 === 0 ? hour : ''}</div>
              </div>
            );
          })}
        </div>
      </section>

      <div className="alh-filters" role="group" aria-label="Filter alerts">
        <label className="alh-field">
          <span>Rule</span>
          <select value={selectedRule} onChange={(e) => setSelectedRule(e.target.value)}>
            <option value="all">Every rule</option>
            {alertRules.map(rule => <option key={rule.rule_id} value={rule.rule_id}>{rule.name}</option>)}
          </select>
        </label>
        <label className="alh-field">
          <span>Severity</span>
          <select value={filterSeverity} onChange={(e) => setFilterSeverity(e.target.value)}>
            <option value="all">Every severity</option>
            <option value="critical">Critical</option>
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
        </label>
        <label className="alh-field">
          <span>Detection</span>
          <select value={filterType} onChange={(e) => setFilterType(e.target.value)}>
            <option value="all">Every kind</option>
            <option value="ml">Model</option>
            <option value="threshold">Threshold</option>
            <option value="statistical">Drift</option>
          </select>
        </label>
        {sensorFilter && (
          <button
            type="button"
            className="alh-sensor-chip"
            aria-label={`Clear the sensor filter ${sensorFilterName}`}
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              next.delete('sensor');
              setSearchParams(next, { replace: true });
            }}
          >
            Sensor {sensorFilterName} <span aria-hidden="true">✕</span>
          </button>
        )}
      </div>

      <section aria-labelledby="alh-open-h">
        <h2 id="alh-open-h" className="alh-h">Open <span className="alh-count mono">{open.length}</span></h2>
        {open.length === 0
          ? <p className="alh-empty">Nothing is open{filteredAlerts.length !== alerts.length ? ' for these filters' : ''}.</p>
          : <ul className="alh-list">{open.map(row)}</ul>}
      </section>

      {earlier.length > 0 && (
        <section aria-labelledby="alh-earlier-h">
          <h2 id="alh-earlier-h" className="alh-h">Earlier</h2>
          <ul className="alh-list">{earlier.slice(0, 30).map(row)}</ul>
        </section>
      )}
    </div>
  );
}

export default AlertHistory;
