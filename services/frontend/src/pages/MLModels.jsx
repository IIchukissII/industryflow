// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useMemo, useState } from 'react';
import Icon from '../components/Icon';
import authFetch from '../services/http';
import { fmtMetric, pickPrimary } from '../utils/metrics';
import ModelUploadWizard from './ModelUploadWizard';
import './MLModels.css';

// Two orthogonal facts get conflated the moment you call them both "source", and one of the two
// is a lie waiting to happen (ADR-0030):
//
//   • WHICH REGISTRY the row was read from — a behavioural fact (`registry`) that decides what the
//     drawer can do. A notebook-registry model (/api/registered-models, MLflow) carries version
//     history and an editable description; a platform-registry model (/api/models) carries a deploy
//     control and an on-demand drift check.
//   • WHERE THE MODEL WAS AUTHORED — the fact an operator actually reads off the row (`origin`).
//     ADR-0030 dec 8 made this first-class as *provenance*: a model the platform watched being made
//     (kernel-authored) versus one brought in from outside (uploaded). An uploaded model lands in
//     the platform registry, so labelling it by registry alone prints it as "Platform" — the exact
//     opposite of what it is, the least platform-authored thing on the page.
//
// So the two are kept apart on purpose: `registry` gates behaviour, `origin` is what we show.
const ORIGIN_LABEL = { notebook: 'Notebook', platform: 'Platform', uploaded: 'Uploaded' };

// `origin` = `registry` for a notebook or platform model, splitting out `uploaded` when provenance
// says so. Provenance is read off how the model arrived (ADR-0030 dec 8), never asked; a platform
// row with no provenance predates the distinction and is a platform detector, never an upload.
function originOf(registry, provenance) {
  if (registry === 'notebook') return 'notebook';
  return provenance === 'uploaded' ? 'uploaded' : 'platform';
}

function fmtDate(ms) {
  if (!ms) return '—';
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: '2-digit' });
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
    registry: 'notebook',
    origin: 'notebook',
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
  // ADR-0030 dec 8: provenance now rides on /api/models. null means "predates the distinction"
  // (a platform detector), which is not the same as unknown origin — see originOf.
  const provenance = m.provenance || null;
  return {
    key: `platform:${m.model_id || m.name}`,
    id: m.model_id,
    name: m.name || m.model_name,
    registry: 'platform',
    provenance,
    origin: originOf('platform', provenance),
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
    // ADR-0027: does the serving environment still satisfy what this model declares? Surfaced here
    // and NOT as an alert — it is a mechanical fact about two containers, not the statistical claim
    // about the world that ADR-0022's retrain lane carries. null means "never evaluated" (registered
    // before ADR-0027), which is not the same as "fine".
    compatibility: m.compatibility_status || null,
    compatibilityDetail: m.compatibility_detail || null,
    raw: m,
  };
}

// The three verdicts (ADR-0027 dec 7), mapped onto the reserved status palette. `compatible` carries
// no row badge on purpose — a healthy model should be quiet; only a finding earns the reader's eye.
const COMPAT = {
  incompatible: {
    pill: 'badge-crit',
    label: 'incompatible',
    title: 'This serving environment cannot honour what the model declares — it will not deploy (ADR-0027).',
    headline: 'This environment cannot serve this model',
    note: 'The serving environment does not satisfy what the model declares, so it will not be deployed. Loading it anyway risks scoring silently wrong, which is worse than refusing it.',
  },
  patch_drift: {
    pill: 'badge-warn',
    label: 'version drift',
    title: 'Servable, but the training and serving versions are not identical — scikit-learn warns on load (ADR-0027).',
    headline: 'Trained against different versions',
    note: 'The model is servable: the versions differ, but not in a way that breaks the contract. scikit-learn warns when it loads a model built by another version, and that warning is shown rather than swallowed.',
  },
  compatible: {
    pill: 'badge-live',
    label: 'parity',
    title: 'The serving environment satisfies everything this model declares (ADR-0027).',
    headline: 'The serving environment satisfies this model',
    note: null,
  },
};

/**
 * The two manifests, reconciled line by line.
 *
 * The whole content here is a comparison, so it is drawn as one: library, what it was trained
 * against, what would serve it. A library that agrees stays quiet in mono; one that breaks the
 * contract is marked and carries its reason on the same line, so "which library, which direction"
 * is answerable at a glance instead of parsed out of a sentence. An absent library shows an em-dash
 * in the serving column — the gap in the column IS the finding, which is exactly how a torch model
 * arriving at an image with no torch should read.
 */
function ParityLedger({ status, detail }) {
  const meta = COMPAT[status];
  if (!meta) return null;

  const declared = detail?.declared || {};
  const present = detail?.present || {};
  const faults = detail?.faults || {};
  const libraries = Object.keys(declared).sort();

  return (
    <section className="mdl-block">
      <div className="mdl-block-head">
        <span className="eyebrow">Environment parity</span>
        <span className={`badge ${meta.pill}`}>{meta.label}</span>
      </div>

      <p className={`mdl-compat-head${status === 'incompatible' ? ' crit' : ''}`}>{meta.headline}</p>
      {meta.note && <p className="mdl-compat-note">{meta.note}</p>}

      {detail?.flavor && (
        <p className="mdl-compat-flavor mono">
          {detail.flavor}
          {detail.flavor_supported === false && <span className="mdl-compat-missing"> · not installed here</span>}
        </p>
      )}

      {libraries.length > 0 && (
        <div className="mdl-ledger" role="table" aria-label="Trained-against versus serving versions">
          <div className="mdl-ledger-row mdl-ledger-head" role="row">
            <span role="columnheader">library</span>
            <span role="columnheader">trained against</span>
            <span role="columnheader">serving with</span>
          </div>
          {libraries.map((lib) => {
            const fault = faults[lib];
            const serving = present[lib];
            // A version that differs without breaking the contract still deserves to be findable —
            // in the drift case it IS the whole finding, and an unmarked ledger would make the
            // reader diff two columns by eye to locate it.
            const drifted = !fault && serving && serving !== declared[lib];
            return (
              <div key={lib} className={`mdl-ledger-row${fault ? ' fault' : ''}`} role="row">
                <span className="mdl-ledger-lib mono" role="cell">{lib}</span>
                {/* What the model was trained against is never "wrong" — the model is a fact. It is
                    THIS ENVIRONMENT that fails to meet it, so only the serving side is marked. That
                    also makes the direction of the fix readable straight off the row. */}
                <span className="mdl-ledger-v mono" role="cell">{declared[lib]}</span>
                <span
                  className={`mdl-ledger-v mono${fault ? ' bad' : ''}${drifted ? ' drift' : ''}`}
                  role="cell"
                >
                  {serving || <span className="mdl-compat-missing">absent</span>}
                </span>
                {fault && <span className="mdl-ledger-why" role="cell">{fault}</span>}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

function MLModels() {
  const [models, setModels] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [selected, setSelected] = useState(null); // the row whose drawer is open
  const [detail, setDetail] = useState(null); // fetched detail for the selected notebook model
  const [filter, setFilter] = useState('all'); // all | notebook | platform | uploaded
  const [retrainModels, setRetrainModels] = useState(() => new Set()); // model_ids with a live retrain rec (ADR-0022)
  const [uploading, setUploading] = useState(false); // the ADR-0030 upload wizard is open

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nbRes, plRes, alRes] = await Promise.allSettled([
        authFetch('/api/registered-models'),
        authFetch('/api/models'),
        authFetch('/api/alerts?condition=retrain_recommended&acknowledged=false&limit=200'), // just the live retrain recs
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
        // Server-filtered to unacknowledged retrain_recommended alerts; just collect their models.
        setRetrainModels(new Set((alerts || []).filter((a) => a.model_id).map((a) => a.model_id)));
      }
      out.sort((a, b) => (b.updated || 0) - (a.updated || 0));
      setModels(out);
      if (!out.length && nbRes.status === 'rejected' && plRes.status === 'rejected') {
        setError('Could not reach the model registry.');
      }
      return out; // so a caller (the upload wizard) can open a just-registered row from the reload
    } catch {
      setError('Could not load models.');
      return [];
    } finally {
      setLoading(false);
    }
  }, []);

  // Load on mount. The fetch is invoked from an inner async function so the effect body itself
  // starts no synchronous state update — the data arrives (and setState runs) off the sync path.
  useEffect(() => { (async () => { await load(); })(); }, [load]);

  // When a notebook model is opened, pull its full version history + per-version run metrics.
  const openRow = useCallback(async (row) => {
    setSelected(row);
    setDetail(null);
    if (row.registry !== 'notebook') return;
    try {
      const res = await authFetch(`/api/registered-models/${encodeURIComponent(row.name)}`);
      if (res.ok) setDetail(await res.json());
    } catch { /* drawer still renders summary without history */ }
  }, []);

  // A model just registered through the upload wizard: reload, and open its drawer from the reload's
  // own result (not via an effect watching state) so the operator lands on it — and can deploy it,
  // the load-and-score gate (ADR-0030 dec 7). load() returns the freshly-built rows for exactly this.
  const handleRegistered = useCallback(async (created) => {
    setUploading(false);
    const rows = await load();
    const row = (rows || []).find((m) => m.id === created?.model_id);
    if (row) openRow(row);
  }, [load, openRow]);

  const counts = useMemo(() => ({
    all: models.length,
    notebook: models.filter((m) => m.origin === 'notebook').length,
    platform: models.filter((m) => m.origin === 'platform').length,
    uploaded: models.filter((m) => m.origin === 'uploaded').length,
    deployed: models.filter((m) => statusTone(m.status).dot === 'ok').length,
  }), [models]);

  const shown = filter === 'all' ? models : models.filter((m) => m.origin === filter);

  return (
    <>
      <div className="page-head">
        <div>
          <div className="eyebrow">Model registry</div>
          <h1>Models</h1>
          <div className="sub">Every model serving your tenant — authored in notebooks, built in, or brought in from outside.</div>
        </div>
        <div className="mdl-head-actions">
          <button className="btn btn-secondary btn-sm" onClick={load} disabled={loading}>
            <Icon name="refresh" size={14} /> {loading ? 'Loading…' : 'Refresh'}
          </button>
          {/* ADR-0030: bring in a model trained off-platform, through the one mediated door. */}
          <button className="btn btn-primary btn-sm" onClick={() => setUploading(true)}>
            <Icon name="upload" size={14} /> Upload model
          </button>
        </div>
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
        {/* ADR-0030 dec 8 wants the count of externally-authored models SEEN, not buried — an
            operator should be able to answer "how many models am I trusting from outside?" at a
            glance. It takes the headline slot the low-value Platform tile held; the platform count
            still lives on the filter tab below. */}
        <div className="kpi" style={{ '--accent': 'var(--gold)' }}>
          <div className="kpi-label">Uploaded</div>
          <div className="kpi-value">{counts.uploaded}</div>
          <div className="kpi-foot">authored off-platform</div>
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
          <div className="mdl-filters" role="tablist" aria-label="Filter by origin">
            {['all', 'notebook', 'platform', 'uploaded'].map((f) => (
              <button
                key={f}
                role="tab"
                aria-selected={filter === f}
                className={`mdl-filter${filter === f ? ' active' : ''}`}
                onClick={() => setFilter(f)}
              >
                {f === 'all' ? 'All' : ORIGIN_LABEL[f]} <span className="mdl-filter-n">{counts[f]}</span>
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
            <span className="mdl-empty-hint">Register one from a notebook, train a platform detector, or upload one you built elsewhere.</span>
          </div>
        ) : (
          <div className="mdl-table-wrap">
            <table className="data-table mdl-table">
              <thead>
                <tr>
                  <th>Model</th><th>Origin</th><th>Version</th><th>Stage</th>
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
                        {/* Only a FINDING earns a row badge. A model in parity is the expected case
                            and stays quiet — its verdict is in the drawer for anyone who looks. */}
                        {(m.compatibility === 'incompatible' || m.compatibility === 'patch_drift') && (
                          <span
                            className={`badge ${COMPAT[m.compatibility].pill} mdl-retrain`}
                            title={COMPAT[m.compatibility].title}
                          >
                            <Icon name="alert" size={11} /> {COMPAT[m.compatibility].label}
                          </span>
                        )}
                      </td>
                      {/* Uploaded is the one origin that earns an icon: an externally-authored
                          model is the exceptional case an operator must not skim past, the same way
                          only a finding earns a row badge above. */}
                      <td>
                        <span className={`badge mdl-src-${m.origin}`}>
                          {m.origin === 'uploaded' && <Icon name="upload" size={11} />}
                          {ORIGIN_LABEL[m.origin]}
                        </span>
                      </td>
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
          key={selected.key}
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

      {uploading && (
        <ModelUploadWizard onClose={() => setUploading(false)} onRegistered={handleRegistered} />
      )}
    </>
  );
}

const PLATFORM_STATUSES = ['production', 'active', 'staging', 'archived'];

// Rendered with `key={row.key}` (see the render site), so opening a different model remounts the
// drawer — draft/editing/saveErr initialise fresh from the new row, no reset effect required.
function ModelDrawer({ row, detail, retrainRecommended, onClose, onSaved, onStatusChanged }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(row.description || '');
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState(null);
  const [statusBusy, setStatusBusy] = useState(false);

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
    } catch {
      setSaveErr('Could not save. Try again.');
    } finally {
      setSaving(false);
    }
  };

  // Platform-registry models can be promoted/retired from here — the status gates whether the
  // alerting pipeline serves them (production/active = live).
  const changeStatus = async (status) => {
    setStatusBusy(true);
    try {
      const res = await authFetch(`/api/models/${row.id}/deploy`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model_id: row.id, environment: status }),
      });
      if (res.ok) onStatusChanged(status);
    } catch { /* leave the select on its prior value */ } finally {
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
            <div className="eyebrow">{ORIGIN_LABEL[row.origin]} model</div>
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
          {/* Provenance (ADR-0030 dec 8). Only an uploaded model carries this — a witnessed model
              (notebook or platform) is the ordinary case and stays quiet, its origin already told by
              the header. It frames everything below it: the parity ledger's empirical check is a
              narrower proof for an upload (dec 7), and the drift lane may not cover it (dec 8), so
              the reader needs the frame before the sections. Gold, not a status colour — an external
              origin is a fact to attend to, not a fault to fix. */}
          {row.origin === 'uploaded' && (
            <section className="mdl-prov">
              <div className="mdl-prov-head">
                <Icon name="upload" size={15} color="var(--gold)" />
                <span className="eyebrow mdl-prov-eyebrow">Provenance</span>
              </div>
              <p className="mdl-prov-title">Authored outside the platform</p>
              <p className="mdl-prov-note">
                This model was trained somewhere the platform never watched. It was admitted through
                the upload path: the artifact carries no executable code, its declared output
                semantics were accepted, and it loaded and scored in this serving environment before
                it could deploy.
              </p>
              <p className="mdl-prov-note">
                That is a narrower proof than a notebook or platform model carries — the platform can
                confirm this model runs <em>here</em>, not that here matches where it was trained.
              </p>
            </section>
          )}

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

          {/* Environment parity (ADR-0027). Rendered as a LEDGER, not a warning box, because that is
              what it actually is: a reconciliation of two manifests — what the model was trained
              against, and what would serve it — reconciled library by library. Prose bullets would
              throw that structure away and make the reader rebuild the table in their head.

              Deliberately not an alert (ADR-0027 dec 8): a version mismatch is a mechanical fact
              about two containers, with a different remedy from the statistical retrain lane above —
              the weights may be perfectly fine. */}
          {row.compatibility && <ParityLedger status={row.compatibility} detail={row.compatibilityDetail} />}

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

          {/* Data drift — a platform-registry model can be scored against its training baseline on
              demand (ADR-0021). Heavy compute, so it's never auto-run: the operator asks for it. An
              uploaded model without a declared baseline honestly reports "unavailable" here rather
              than a fake zero — which is ADR-0030 dec 8's "visibly un-monitorable" in the flesh. */}
          {row.registry === 'platform' && <DriftPanel row={row} />}

          {/* Source lineage. */}
          {row.runId && (
            <section className="mdl-block">
              <span className="eyebrow">Source run</span>
              <p className="mono mdl-runid">{row.runId}</p>
            </section>
          )}

          {/* Version history — only notebook-registry (MLflow) models carry their full lineage. */}
          {row.registry === 'notebook' && (
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

          {/* Platform-registry models (built-in detectors and admitted uploads alike): equipment
              binding + a deploy control that gates live serving. */}
          {row.registry === 'platform' && (
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
  // A different model starts fresh: the drawer is keyed by row, so selecting another model
  // remounts this panel — no reset effect needed.

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
    } catch {
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
