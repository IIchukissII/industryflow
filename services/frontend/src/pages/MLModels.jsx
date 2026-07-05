// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useMemo, useState } from 'react';
import Icon from '../components/Icon';
import authFetch from '../services/http';
import './MLModels.css';

// The registry shows two kinds of tenant model side by side, normalised to one row shape:
//   • notebook  — MLflow registered models a data scientist authored, read through the safe
//                 tenant-scoped /api/registered-models path (names already stripped to plain).
//   • platform  — the built-in anomaly detectors in /api/models, used by the alerting pipeline.
const SOURCE_LABEL = { notebook: 'Notebook', platform: 'Platform' };

// Headline metric: the one number worth showing in the table. Prefer well-known keys, else the
// first metric the run logged — so every model still shows *something* instrument-like.
const PRIMARY_METRIC_ORDER = ['f1', 'f1_score', 'accuracy', 'auc', 'auc_roc', 'r2', 'rmse', 'mae', 'mape'];

function fmtMetric(v) {
  if (v === null || v === undefined || v === '') return '—';
  const n = Number(v);
  if (Number.isNaN(n)) return String(v);
  if (Number.isInteger(n)) return String(n);
  return Math.abs(n) >= 1000 ? n.toFixed(0) : n.toFixed(4).replace(/0+$/, '').replace(/\.$/, '');
}

function fmtDate(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: '2-digit' });
}

function pickPrimary(metrics) {
  const keys = Object.keys(metrics || {});
  if (!keys.length) return null;
  const lower = Object.fromEntries(keys.map((k) => [k.toLowerCase(), k]));
  for (const want of PRIMARY_METRIC_ORDER) {
    if (lower[want]) return { label: want.replace(/_/g, ' '), value: metrics[lower[want]] };
  }
  return { label: keys[0].replace(/_/g, ' '), value: metrics[keys[0]] };
}

// status/stage → which signal dot + pill the row wears.
function statusTone(status) {
  const s = (status || '').toLowerCase();
  if (s === 'production' || s === 'active') return { dot: 'ok', pill: 'badge-live' };
  if (s === 'staging') return { dot: 'pending', pill: 'badge-warn' };
  if (s === 'archived' || s === 'none' || s === '') return { dot: '', pill: '' };
  return { dot: 'pending', pill: '' };
}

function normalizeNotebook(m) {
  return {
    key: `notebook:${m.name}`,
    name: m.name,
    source: 'notebook',
    version: m.latest_version ? `v${m.latest_version}` : '—',
    status: m.stage || 'None',
    updated: m.last_updated_timestamp || m.creation_timestamp || null,
    description: m.description || '',
    editable: true,
    metrics: m.metrics || {},
    primary: pickPrimary(m.metrics),
    runId: m.run_id || null,
    raw: m,
  };
}

function normalizePlatform(m) {
  const metrics = {};
  ['accuracy', 'precision_score', 'recall', 'f1_score', 'auc_roc'].forEach((k) => {
    if (m[k] !== null && m[k] !== undefined) metrics[k.replace('_score', '')] = m[k];
  });
  const updated = m.deployed_at || m.training_date || m.created_at || null;
  return {
    key: `platform:${m.model_id || m.name}`,
    id: m.model_id,
    name: m.name || m.model_name,
    source: 'platform',
    version: m.version ? `v${m.version}` : '—',
    status: m.status || 'unknown',
    updated: updated ? Date.parse(updated) : null,
    description: m.description || '',
    editable: false,
    type: m.model_type,
    equipment: m.equipment_type,
    metrics,
    primary: pickPrimary(metrics),
    runId: m.mlflow_run_id || null,
    raw: m,
  };
}

function MLModels() {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null); // the row whose drawer is open
  const [detail, setDetail] = useState(null); // fetched detail for the selected notebook model
  const [filter, setFilter] = useState('all'); // all | notebook | platform
  const [retrainModels, setRetrainModels] = useState(() => new Set()); // model_ids with a live retrain rec (ADR-0022)

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nbRes, plRes, alRes] = await Promise.allSettled([
        authFetch('/api/registered-models'),
        authFetch('/api/models'),
        authFetch('/api/alerts?severity=high&limit=1000'), // for the retrain-recommended signal
      ]);
      const out = [];
      if (nbRes.status === 'fulfilled' && nbRes.value.ok) {
        const d = await nbRes.value.json();
        (d.models || []).forEach((m) => out.push(normalizeNotebook(m)));
      }
      if (plRes.status === 'fulfilled' && plRes.value.ok) {
        const d = await plRes.value.json();
        (d.models || []).forEach((m) => out.push(normalizePlatform(m)));
      }
      // Models the alert worker has recently recommended retraining (ADR-0022): a distinct
      // 'retrain_recommended' statistical alert carries the model_id. An acknowledged one is
      // treated as handled, so it drops off.
      if (alRes.status === 'fulfilled' && alRes.value.ok) {
        const alerts = await alRes.value.json();
        setRetrainModels(new Set(
          (alerts || [])
            .filter((a) => a.condition === 'retrain_recommended' && a.model_id && !a.acknowledged)
            .map((a) => a.model_id)
        ));
      }
      out.sort((a, b) => (b.updated || 0) - (a.updated || 0));
      setModels(out);
      if (!out.length && nbRes.status === 'rejected' && plRes.status === 'rejected') {
        setError('Could not reach the model registry.');
      }
    } catch (e) {
      setError('Could not load models.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  // When a notebook model is opened, pull its full version history + per-version run metrics.
  const openRow = useCallback(async (row) => {
    setSelected(row);
    setDetail(null);
    if (row.source !== 'notebook') return;
    try {
      const res = await authFetch(`/api/registered-models/${encodeURIComponent(row.name)}`);
      if (res.ok) setDetail(await res.json());
    } catch (e) { /* drawer still renders summary without history */ }
  }, []);

  const counts = useMemo(() => ({
    all: models.length,
    notebook: models.filter((m) => m.source === 'notebook').length,
    platform: models.filter((m) => m.source === 'platform').length,
    deployed: models.filter((m) => statusTone(m.status).dot === 'ok').length,
  }), [models]);

  const shown = filter === 'all' ? models : models.filter((m) => m.source === filter);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Model registry</div>
          <h1>Models</h1>
          <div className="sub">Trained models across your tenant — notebook-authored and platform detectors.</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
          <Icon name="refresh" size={14} /> {loading ? 'Loading…' : 'Refresh'}
        </button>
      </div>

      <div className="kpi-row mdl-kpis">
        <div className="kpi">
          <div className="kpi-label">Total models</div>
          <div className="kpi-value">{counts.all}</div>
          <div className="kpi-foot">in the registry</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--signal)' }}>
          <div className="kpi-label">Notebook</div>
          <div className="kpi-value">{counts.notebook}</div>
          <div className="kpi-foot">authored in notebooks</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--warn)' }}>
          <div className="kpi-label">Platform</div>
          <div className="kpi-value">{counts.platform}</div>
          <div className="kpi-foot">built-in detectors</div>
        </div>
        <div className="kpi" style={{ '--accent': 'var(--live)' }}>
          <div className="kpi-label">In production</div>
          <div className="kpi-value">{counts.deployed}</div>
          <div className="kpi-foot">serving live</div>
        </div>
      </div>

      <section className="panel mdl-panel" style={{ padding: 0 }}>
        <div className="panel-head">
          <h2>Registry</h2>
          <div className="mdl-filters" role="tablist" aria-label="Filter by source">
            {['all', 'notebook', 'platform'].map((f) => (
              <button
                key={f}
                role="tab"
                aria-selected={filter === f}
                className={`mdl-filter${filter === f ? ' active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f === 'all' ? 'All' : SOURCE_LABEL[f]} <span className="mdl-filter-n">{counts[f]}</span>
              </button>
            ))}
          </div>
        </div>

        {loading ? (
          <div className="mdl-empty"><span className="sdot pending" /> Loading registry…</div>
        ) : error ? (
          <div className="mdl-empty"><Icon name="alert" size={22} color="var(--crit)" /><p>{error}</p></div>
        ) : !shown.length ? (
          <div className="mdl-empty">
            <Icon name="cpu" size={26} color="var(--faint)" />
            <p>No models here yet.</p>
            <span className="mdl-empty-hint">Register a model from a notebook, or train a platform detector.</span>
          </div>
        ) : (
          <div className="mdl-table-wrap">
            <table className="data-table mdl-table">
              <thead>
                <tr>
                  <th>Model</th><th>Source</th><th>Version</th><th>Stage</th>
                  <th className="mdl-num">Key metric</th><th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((m) => {
                  const tone = statusTone(m.status);
                  const active = selected && selected.key === m.key;
                  return (
                    <tr
                      key={m.key}
                      className={`mdl-row${active ? ' active' : ''}`}
                      onClick={() => openRow(m)}
                      tabIndex={0}
                      onKeyDown={(e) => { if (e.key === 'Enter') openRow(m); }}
                    >
                      <td className="mdl-name">
                        {m.name}
                        {m.id && retrainModels.has(m.id) && (
                          <span className="badge badge-warn mdl-retrain" title="The alert worker recommends retraining this model (sustained drift + precision decay)">
                            <Icon name="refresh" size={11} /> retrain
                          </span>
                        )}
                      </td>
                      <td><span className={`badge mdl-src-${m.source}`}>{SOURCE_LABEL[m.source]}</span></td>
                      <td className="mono mdl-dim">{m.version}</td>
                      <td>
                        <span className="mdl-stage">
                          {tone.dot && <span className={`sdot ${tone.dot}`} />}
                          {m.status}
                        </span>
                      </td>
                      <td className="mdl-num">
                        {m.primary
                          ? <span className="mono"><i className="mdl-mlabel">{m.primary.label}</i> {fmtMetric(m.primary.value)}</span>
                          : <span className="mdl-dim">—</span>}
                      </td>
                      <td className="mono mdl-dim">{fmtDate(m.updated)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {selected && (
        <ModelDrawer
          row={selected}
          detail={detail}
          retrainRecommended={!!(selected.id && retrainModels.has(selected.id))}
          onClose={() => setSelected(null)}
          onSaved={(desc) => {
            setModels((ms) => ms.map((m) => (m.key === selected.key ? { ...m, description: desc } : m)));
            setSelected((s) => ({ ...s, description: desc }));
          }}
          onStatusChanged={(status) => {
            setModels((ms) => ms.map((m) => (m.key === selected.key ? { ...m, status } : m)));
            setSelected((s) => ({ ...s, status }));
          }}
        />
      )}
    </>
  );
}

const PLATFORM_STATUSES = ['production', 'active', 'staging', 'archived'];

function ModelDrawer({ row, detail, retrainRecommended, onClose, onSaved, onStatusChanged }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(row.description || '');
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(null);
  const [statusBusy, setStatusBusy] = useState(false);

  useEffect(() => { setDraft(row.description || ''); setEditing(false); setSaveErr(null); }, [row.key, row.description]);

  // Esc closes the drawer.
  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const saveDescription = async () => {
    setSaving(true);
    setSaveErr(null);
    try {
      const res = await authFetch(`/api/registered-models/${encodeURIComponent(row.name)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ description: draft }),
      });
      if (!res.ok) throw new Error();
      onSaved(draft);
      setEditing(false);
    } catch (e) {
      setSaveErr('Could not save. Try again.');
    } finally {
      setSaving(false);
    }
  };

  // Platform detectors can be promoted/retired from here — the status gates whether the alerting
  // pipeline serves them (production/active = live).
  const changeStatus = async (status) => {
    setStatusBusy(true);
    try {
      const res = await authFetch(`/api/models/${row.id}/deploy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: row.id, environment: status }),
      });
      if (res.ok) onStatusChanged(status);
    } catch (e) { /* leave the select on its prior value */ } finally {
      setStatusBusy(false);
    }
  };

  const tone = statusTone(row.status);
  const metricEntries = Object.entries(row.metrics || {});
  const versions = detail?.versions || null;

  return (
    <div className="mdl-scrim" onClick={onClose}>
      <aside className="mdl-drawer" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={`${row.name} details`}>
        <div className="mdl-drawer-head">
          <div>
            <div className="eyebrow">{SOURCE_LABEL[row.source]} model</div>
            <h2 className="mdl-drawer-title">{row.name}</h2>
            <div className="mdl-drawer-meta mono">
              {row.version}
              <span className="mdl-sep">·</span>
              <span className="mdl-stage">{tone.dot && <span className={`sdot ${tone.dot}`} />}{row.status}</span>
              {row.type && <><span className="mdl-sep">·</span>{row.type}</>}
            </div>
          </div>
          <button className="icon-btn mdl-close" onClick={onClose} title="Close" aria-label="Close">×</button>
        </div>

        <div className="mdl-drawer-body">
          {/* Retrain recommendation (ADR-0022) — sustained drift + label-derived precision decay. */}
          {retrainRecommended && (
            <div className="mdl-retrain-notice">
              <Icon name="alert" size={16} color="var(--warn)" />
              <div>
                <strong>Retrain recommended</strong>
                <p>Sustained drift together with falling operator-labelled precision suggests this model has decayed. Retrain it via the notebook flow.</p>
              </div>
            </div>
          )}

          {/* Description — editable for notebook models, read-only for platform detectors. */}
          <section className="mdl-block">
            <div className="mdl-block-head">
              <span className="eyebrow">Description</span>
              {row.editable && !editing && (
                <button className="mdl-link" onClick={() => setEditing(true)}>Edit</button>
              )}
            </div>
            {editing ? (
              <div className="mdl-edit">
                <textarea
                  className="mdl-textarea"
                  value={draft}
                  rows={4}
                  placeholder="What this model predicts, how it was trained, when to retrain…"
                  onChange={(e) => setDraft(e.target.value)}
                />
                {saveErr && <div className="mdl-err">{saveErr}</div>}
                <div className="mdl-edit-actions">
                  <button className="btn btn-secondary btn-sm" onClick={() => { setEditing(false); setDraft(row.description || ''); }} disabled={saving}>Cancel</button>
                  <button className="btn btn-primary btn-sm" onClick={saveDescription} disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                </div>
              </div>
            ) : (
              <p className={`mdl-desc${row.description ? '' : ' faint'}`}>
                {row.description || (row.editable ? 'No description yet — add one to explain what this model does.' : 'No description.')}
              </p>
            )}
          </section>

          {/* Metrics — the latest version's run metrics, as instrument readouts. */}
          <section className="mdl-block">
            <span className="eyebrow">Metrics</span>
            {metricEntries.length ? (
              <div className="mdl-metrics">
                {metricEntries.map(([k, v]) => (
                  <div className="mdl-metric" key={k}>
                    <span className="mdl-metric-label">{k.replace(/_/g, ' ')}</span>
                    <span className="mdl-metric-value mono">{fmtMetric(v)}</span>
                  </div>
                ))}
              </div>
            ) : <p className="mdl-desc faint">No metrics logged for this model.</p>}
          </section>

          {/* Data drift — platform detectors carry a reference profile, so we can compare recent
              readings against the training baseline on demand (ADR-0021). Heavy compute, so it's
              never auto-run: the operator asks for it. */}
          {row.source === 'platform' && <DriftPanel row={row} />}

          {/* Source lineage. */}
          {row.runId && (
            <section className="mdl-block">
              <span className="eyebrow">Source run</span>
              <p className="mono mdl-runid">{row.runId}</p>
            </section>
          )}

          {/* Version history — notebook models carry their full lineage. */}
          {row.source === 'notebook' && (
            <section className="mdl-block">
              <span className="eyebrow">Versions</span>
              {versions === null ? (
                <p className="mdl-desc faint">Loading history…</p>
              ) : !versions.length ? (
                <p className="mdl-desc faint">No versions.</p>
              ) : (
                <table className="data-table mdl-versions">
                  <thead><tr><th>Ver</th><th>Stage</th><th>Status</th><th>Created</th></tr></thead>
                  <tbody>
                    {versions.map((v) => {
                      const t = statusTone(v.current_stage);
                      return (
                        <tr key={v.version}>
                          <td className="mono">v{v.version}</td>
                          <td><span className="mdl-stage">{t.dot && <span className={`sdot ${t.dot}`} />}{v.current_stage || 'None'}</span></td>
                          <td className="mono mdl-dim">{v.status || '—'}</td>
                          <td className="mono mdl-dim">{fmtDate(v.creation_timestamp)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </section>
          )}

          {/* Platform detectors: equipment binding + a deploy control that gates live serving. */}
          {row.source === 'platform' && (
            <section className="mdl-block">
              <span className="eyebrow">Deployment</span>
              {row.equipment && <p className="mdl-desc" style={{ marginBottom: 12 }}>Equipment type: {row.equipment}</p>}
              <label className="mdl-status-ctl">
                <span className="mdl-status-label">Status</span>
                <select
                  className="mdl-select"
                  value={PLATFORM_STATUSES.includes((row.status || '').toLowerCase()) ? row.status.toLowerCase() : 'active'}
                  disabled={statusBusy || !row.id}
                  onChange={(e) => changeStatus(e.target.value)}
                >
                  {PLATFORM_STATUSES.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </label>
            </section>
          )}
        </div>
      </aside>
    </div>
  );
}

// Trailing window → a human label ("last 24h") for the drift readout.
function fmtWindow(minutes) {
  if (!minutes) return '';
  if (minutes % 1440 === 0) { const d = minutes / 1440; return `last ${d} day${d > 1 ? 's' : ''}`; }
  if (minutes % 60 === 0) { const h = minutes / 60; return `last ${h} hour${h > 1 ? 's' : ''}`; }
  return `last ${minutes} min`;
}

// Map a drift result to the reserved status palette (tone → sdot/badge class), an icon, and a
// label — colour is never the only signal (icon + words carry it too).
const DRIFT_TONES = {
  ok:   { dot: 'ok',      badge: 'badge-live', icon: 'check' },
  warn: { dot: 'pending', badge: 'badge-warn', icon: 'alert' },
  crit: { dot: 'bad',     badge: 'badge-crit', icon: 'alert' },
  idle: { dot: '',        badge: '',           icon: 'help' },
};

function driftVerdict(result) {
  const status = result?.status;
  if (status === 'ok') {
    const detected = !!(result.data_drift && result.data_drift.drift_detected);
    return detected
      ? { tone: 'warn', label: 'Drift detected' }
      : { tone: 'ok', label: 'No drift' };
  }
  if (status === 'error') return { tone: 'crit', label: 'Evaluation failed' };
  if (status === 'insufficient_data') return { tone: 'idle', label: 'Not enough data' };
  return { tone: 'idle', label: 'Unavailable' }; // "unavailable" or any unknown status
}

// On-demand data-drift check for a platform detector. Compares recent readings against the model's
// training baseline via /api/drift/evaluate (evidently on the ml-service). Button-triggered: the
// windowed compute is expensive, so it runs only when the operator asks.
function DriftPanel({ row }) {
  const [state, setState] = useState('idle'); // idle | loading | done | error
  const [result, setResult] = useState(null);
  const [err, setErr] = useState(null);

  // A different model opened in the drawer starts fresh.
  useEffect(() => { setState('idle'); setResult(null); setErr(null); }, [row.id]);

  const evaluate = useCallback(async () => {
    if (!row.id) return;
    setState('loading');
    setErr(null);
    try {
      const res = await authFetch('/api/drift/evaluate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: row.id }),
      });
      if (!res.ok) throw new Error(String(res.status));
      setResult(await res.json());
      setState('done');
    } catch (e) {
      setErr('Could not evaluate drift. Try again.');
      setState('error');
    }
  }, [row.id]);

  const verdict = result ? driftVerdict(result) : null;
  const tone = verdict ? DRIFT_TONES[verdict.tone] : null;
  const dd = result?.data_drift || null;
  const share = dd && typeof dd.drift_share === 'number' ? dd.drift_share : null;
  const threshold = typeof result?.threshold === 'number' ? result.threshold : null;
  const driftedCols = dd?.per_column
    ? Object.entries(dd.per_column).filter(([, c]) => c && c.drifted).map(([name]) => name)
    : [];
  const predDrift = result?.prediction_drift;

  return (
    <section className="mdl-block">
      <div className="mdl-block-head">
        <span className="eyebrow">Data drift</span>
        {state === 'done' && (
          <button className="mdl-link" onClick={evaluate}>Re-evaluate</button>
        )}
      </div>

      {state === 'idle' && (
        <div className="mdl-drift-idle">
          <p className="mdl-desc faint">
            Compare recent readings against this model’s training baseline to check for data drift.
          </p>
          <button className="btn btn-secondary btn-sm" onClick={evaluate} disabled={!row.id}>
            <Icon name="activity" size={14} /> Evaluate drift
          </button>
        </div>
      )}

      {state === 'loading' && (
        <div className="mdl-drift-loading"><span className="sdot pending" /> Evaluating recent window…</div>
      )}

      {state === 'error' && (
        <div className="mdl-drift-idle">
          <p className="mdl-err" style={{ margin: 0 }}>{err}</p>
          <button className="btn btn-secondary btn-sm" onClick={evaluate}>
            <Icon name="refresh" size={14} /> Retry
          </button>
        </div>
      )}

      {state === 'done' && verdict && (
        <div className="mdl-drift">
          <div className="mdl-drift-verdict">
            <span className={`badge ${tone.badge}`.trim()}>
              <Icon name={tone.icon} size={12} /> {verdict.label}
            </span>
            {result.window_minutes ? <span className="mdl-drift-win mono">{fmtWindow(result.window_minutes)}</span> : null}
          </div>

          {result.status === 'ok' && share !== null ? (
            <>
              {/* Drifted-feature share: a proportion meter, filled to the drifted share, with a
                  tick where the alert threshold sits — so the verdict is legible, not just asserted. */}
              <div className="mdl-drift-share">
                <div className="mdl-drift-share-top">
                  <span className="mdl-drift-pct mono">{Math.round(share * 100)}%</span>
                  <span className="mdl-drift-cap">
                    {dd.n_drifted}/{dd.n_columns} feature{dd.n_columns === 1 ? '' : 's'} drifted
                  </span>
                </div>
                <div className="mdl-meter" role="img"
                  aria-label={`${Math.round(share * 100)} percent of features drifted${threshold !== null ? `, threshold ${Math.round(threshold * 100)} percent` : ''}`}>
                  <div className={`mdl-meter-fill tone-${verdict.tone}`} style={{ width: `${Math.min(100, Math.max(2, share * 100))}%` }} />
                  {threshold !== null && (
                    <div className="mdl-meter-tick" style={{ left: `${Math.min(100, threshold * 100)}%` }} title={`Threshold ${Math.round(threshold * 100)}%`} />
                  )}
                </div>
              </div>

              {driftedCols.length > 0 && (
                <div className="mdl-drift-cols">
                  {driftedCols.slice(0, 8).map((c) => (
                    <span className="mdl-drift-chip mono" key={c}>{c}</span>
                  ))}
                  {driftedCols.length > 8 && <span className="mdl-drift-chip mono faint">+{driftedCols.length - 8}</span>}
                </div>
              )}

              {predDrift && predDrift.drifted && (
                <p className="mdl-drift-pred">
                  <Icon name="alert" size={12} color="var(--warn)" /> Prediction drift on{' '}
                  <span className="mono">{predDrift.column}</span>
                </p>
              )}
            </>
          ) : (
            // unavailable / insufficient_data / error — honest note, not a fake zero.
            <p className="mdl-desc faint" style={{ marginTop: 0 }}>
              {result.reason || 'No drift signal is available for this model.'}
            </p>
          )}
        </div>
      )}
    </section>
  );
}

export default MLModels;
