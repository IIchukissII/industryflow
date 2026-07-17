// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Icon from '../components/Icon';
import authFetch from '../services/http';
import './ModelUploadWizard.css';

// ADR-0030: a model trained outside the platform comes back through one mediated door — mint an
// upload capability, stage the artifact, PUT the bytes straight to the store, commit (structural
// admission), then register (the serving side judges declared semantics + compatibility). Decision
// 10 (rev 2) made that reachable from the browser, same-origin, at the edge. This wizard drives it.

// Human labels for the semantics vocabulary the registry advertises. The VALUES come from the server
// (/api/ml/capabilities) so a new detector's semantics appears without a frontend change; these are
// only prose for the ones we know — an unknown key falls back to itself.
const SEMANTICS_COPY = {
  anomaly_probability: 'A calibrated probability that a reading is anomalous.',
  outlier_score: 'A continuous novelty score — more negative means more anomalous.',
  reconstruction_error: "How far the model's reconstruction sits from the input.",
  direct_score: 'The model already emits a 0–1 anomaly score.',
};

const prettySemantics = (s) => s.replace(/_/g, ' ');

// The manifest the gateway requires to judge what the artifact is (ADR-0030 dec 4).
const MANIFEST_NAME = 'MLmodel';

// The member name the gateway keys a file by: a folder pick carries a relative path (strip the
// chosen root); a multi-file pick carries just the name.
function memberName(file) {
  const rel = file.webkitRelativePath;
  if (rel && rel.includes('/')) return rel.slice(rel.indexOf('/') + 1);
  return file.name;
}

async function refusalText(res) {
  // A refusal carries a reason (ADR-0027 dec 2). It may be a plain string or {message, …}.
  try {
    const body = await res.json();
    const d = body.detail !== undefined ? body.detail : body;
    if (typeof d === 'string') return d;
    if (d && typeof d.message === 'string') return d.message;
    return JSON.stringify(d);
  } catch {
    return `The server refused with status ${res.status}.`;
  }
}

// The five stages, in order. Each renders a row in the running view.
const STAGES = [
  { key: 'mint', label: 'Mint upload capability', note: 'a one-upload handle, held only in memory' },
  { key: 'stage', label: 'Stage the artifact', note: 'the gateway keys each file into the staging area' },
  { key: 'transfer', label: 'Transfer the bytes', note: 'straight to the store, not through the mediator' },
  { key: 'admit', label: 'Admit the artifact', note: 'refused if it carries executable object code' },
  { key: 'register', label: 'Register the model', note: 'the serving side judges semantics and compatibility' },
];

function ModelUploadWizard({ onClose, onRegistered }) {
  const [phase, setPhase] = useState('declare'); // declare | running | done | error
  const [files, setFiles] = useState([]);
  const [dragOver, setDragOver] = useState(false);
  const [meta, setMeta] = useState({ name: '', version: '1', type: '', equipment: '', semantics: '' });
  const [caps, setCaps] = useState(null); // fetched capabilities, or 'error'
  const [status, setStatus] = useState({}); // stage.key -> 'run' | 'ok' | 'fail'
  const [transferred, setTransferred] = useState(0);
  const [failure, setFailure] = useState(null); // { stage, msg, refusal }
  const [result, setResult] = useState(null); // registered model
  const fileInput = useRef(null);
  const cap = useRef(null); // the handle — in memory only, never persisted (ADR-0030 dec 10)

  // What this deployment can actually score, so the operator declares a semantics that has a home.
  useEffect(() => {
    (async () => {
      try {
        const res = await authFetch('/api/ml/capabilities');
        if (!res.ok) throw new Error();
        setCaps(await res.json());
      } catch {
        setCaps('error');
      }
    })();
  }, []);

  // The semantics the deployment has a detector for — declaring one it can't score would only earn a
  // refusal at registration, so offer the set that can pass.
  const semanticsOptions = useMemo(() => {
    if (!caps || caps === 'error') return null;
    const set = new Set((caps.detectors || []).map((d) => d.semantics).filter(Boolean));
    return [...set].sort();
  }, [caps]);

  const hasManifest = files.some((f) => memberName(f) === MANIFEST_NAME);
  const canSubmit = files.length > 0 && hasManifest && meta.name.trim() && meta.type.trim() && meta.semantics;

  const addFiles = useCallback((incoming) => {
    setFiles((prev) => {
      const byKey = new Map(prev.map((f) => [memberName(f), f]));
      for (const f of incoming) byKey.set(memberName(f), f);
      return [...byKey.values()];
    });
  }, []);

  const onDrop = useCallback((e) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer?.files?.length) addFiles([...e.dataTransfer.files]);
  }, [addFiles]);

  const run = useCallback(async () => {
    setPhase('running');
    setFailure(null);
    setTransferred(0);
    const mark = (k, s) => setStatus((prev) => ({ ...prev, [k]: s }));
    const fail = (stage, msg, refusal = false) => { mark(stage, 'fail'); setFailure({ stage, msg, refusal }); setPhase('error'); };

    try {
      // 1. Mint — the platform session becoming an upload (ADR-0030 dec 3).
      mark('mint', 'run');
      const mintRes = await authFetch('/api/models/upload-capability', { method: 'POST' });
      if (mintRes.status === 501) return fail('mint', 'This deployment does not accept uploaded models.');
      if (!mintRes.ok) return fail('mint', await refusalText(mintRes));
      const mint = await mintRes.json();
      cap.current = mint.capability;
      mark('mint', 'ok');

      const authHeaders = { Authorization: `Bearer ${cap.current}`, 'Content-Type': 'application/json' };

      // 2. Stage — the gateway names every key; we name the members.
      mark('stage', 'run');
      const members = files.map(memberName);
      const stageRes = await fetch('/api/2.0/industryflow-upload/stage', {
        method: 'POST', headers: authHeaders, body: JSON.stringify({ files: members }),
      });
      if (!stageRes.ok) return fail('stage', await refusalText(stageRes));
      const staged = await stageRes.json();

      // Decision 10's reachability, checked honestly: a pre-signed URL that is not same-origin cannot
      // be PUT to from here — which is what an unconfigured deployment (no PUBLIC_BASE_URL) produces.
      const sampleUrl = new URL(Object.values(staged.urls)[0], window.location.href);
      if (sampleUrl.host !== window.location.host) {
        return fail('stage', 'Browser upload is not configured for this deployment: the object store is not reachable at this origin. Upload from the CLI instead — see docs/architecture/model-upload.md.');
      }
      mark('stage', 'ok');

      // 3. Transfer — bytes go straight to the store under the pre-signed URL; no session credential
      // rides along (credentials omitted), the URL carries its own authorisation (ADR-0019 dec 6).
      mark('transfer', 'run');
      for (const f of files) {
        const url = staged.urls[memberName(f)];
        const putRes = await fetch(url, { method: 'PUT', body: f, credentials: 'omit' });
        if (!putRes.ok) return fail('transfer', `Could not upload ${memberName(f)} (${putRes.status}).`);
        setTransferred((n) => n + 1);
      }
      mark('transfer', 'ok');

      // 4. Commit — structural admission (ADR-0030 dec 4). A 422 here is a reasoned refusal.
      mark('admit', 'run');
      const commitRes = await fetch('/api/2.0/industryflow-upload/commit', {
        method: 'POST', headers: authHeaders, body: JSON.stringify({ upload_id: staged.upload_id }),
      });
      if (!commitRes.ok) return fail('admit', await refusalText(commitRes), commitRes.status === 422);
      const committed = await commitRes.json();
      mark('admit', 'ok');

      // 5. Register — the serving side judges declared semantics + compatibility (ADR-0030 dec 5/6).
      // Left un-deployed: the load-and-score gate (dec 7) runs when the operator deploys it.
      mark('register', 'run');
      const regRes = await authFetch('/api/models', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_name: meta.name.trim(),
          model_version: meta.version.trim() || '1',
          model_type: meta.type.trim(),
          equipment_type: meta.equipment.trim() || null,
          artifact_uri: committed.artifact_uri,
          score_semantics: meta.semantics,
        }),
      });
      if (!regRes.ok) return fail('register', await refusalText(regRes), regRes.status === 422);
      mark('register', 'ok');
      setResult(await regRes.json());
      setPhase('done');
    } catch {
      // A thrown error is a transport failure, not a reasoned refusal.
      const running = STAGES.map((s) => s.key).find((k) => status[k] === 'run') || 'mint';
      fail(running, 'The upload could not complete — a step could not be reached. Check your connection and try again.');
    }
  }, [files, meta, status]);

  return (
    <div className="uw-scrim" onClick={onClose}>
      <div className="uw-modal" onClick={(e) => e.stopPropagation()} role="dialog" aria-label="Upload a model">
        <div className="uw-head">
          <div>
            <div className="eyebrow uw-eyebrow"><Icon name="upload" size={13} /> Upload a model</div>
            <h2 className="uw-title">Bring in a model trained elsewhere</h2>
          </div>
          <button className="icon-btn uw-close" onClick={onClose} aria-label="Close">×</button>
        </div>

        {phase === 'declare' && (
          <div className="uw-body">
            <p className="uw-lede">
              A model trained outside the platform is admitted through one mediated door: its artifact
              carries no executable code, you declare what its output means, and it must load and score
              here before it can serve. Point at the model directory or its files — the <span className="mono">MLmodel</span> manifest must be among them.
            </p>

            {/* File selection */}
            <div
              className={`uw-drop${dragOver ? ' over' : ''}`}
              onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
              onDragLeave={() => setDragOver(false)}
              onDrop={onDrop}
              onClick={() => fileInput.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => { if (e.key === 'Enter') fileInput.current?.click(); }}
            >
              <Icon name="upload" size={22} color="var(--gold)" />
              <p className="uw-drop-title">Drop the model here, or choose files</p>
              <p className="uw-drop-hint">The artifact's files, including its <span className="mono">MLmodel</span> manifest</p>
              <input
                ref={fileInput}
                type="file"
                multiple
                webkitdirectory=""
                style={{ display: 'none' }}
                onChange={(e) => { addFiles([...e.target.files]); e.target.value = ''; }}
              />
            </div>

            {files.length > 0 && (
              <div className="uw-files">
                {files.map((f) => {
                  const m = memberName(f);
                  return (
                    <div className="uw-file" key={m}>
                      <span className={`uw-file-name mono${m === MANIFEST_NAME ? ' manifest' : ''}`}>{m}</span>
                      <span className="uw-file-size mono">{(f.size / 1024).toFixed(0)} KB</span>
                      <button className="uw-file-x" onClick={() => setFiles((p) => p.filter((x) => memberName(x) !== m))} aria-label={`Remove ${m}`}>×</button>
                    </div>
                  );
                })}
                {!hasManifest && (
                  <p className="uw-warn"><Icon name="alert" size={13} color="var(--warn)" /> No <span className="mono">MLmodel</span> manifest yet — the platform can't tell what this artifact is without it.</p>
                )}
              </div>
            )}

            {/* Declarations */}
            <div className="uw-grid">
              <label className="uw-field">
                <span className="uw-label">Model name</span>
                <input className="uw-input" value={meta.name} onChange={(e) => setMeta({ ...meta, name: e.target.value })} placeholder="yield-forecaster-xl" />
              </label>
              <label className="uw-field uw-narrow">
                <span className="uw-label">Version</span>
                <input className="uw-input mono" value={meta.version} onChange={(e) => setMeta({ ...meta, version: e.target.value })} placeholder="1" />
              </label>
              <label className="uw-field">
                <span className="uw-label">Model type</span>
                <input className="uw-input" value={meta.type} onChange={(e) => setMeta({ ...meta, type: e.target.value })} placeholder="gradient_boosting" />
              </label>
              <label className="uw-field">
                <span className="uw-label">Equipment type <span className="uw-opt">optional</span></span>
                <input className="uw-input" value={meta.equipment} onChange={(e) => setMeta({ ...meta, equipment: e.target.value })} placeholder="grow-cabinet" />
              </label>
            </div>

            {/* Declared semantics (ADR-0028) — the fact that cannot be recovered from the output. */}
            <div className="uw-sem">
              <span className="uw-label">What does its output mean? <span className="uw-opt">declared, never guessed</span></span>
              {semanticsOptions === null ? (
                caps === 'error' ? (
                  <input className="uw-input" value={meta.semantics} onChange={(e) => setMeta({ ...meta, semantics: e.target.value })} placeholder="Couldn't load the vocabulary — type the declared semantics" />
                ) : (
                  <p className="uw-desc faint">Loading the vocabulary this deployment advertises…</p>
                )
              ) : semanticsOptions.length === 0 ? (
                <p className="uw-warn"><Icon name="alert" size={13} color="var(--warn)" /> This deployment has no detector loaded, so it can't score any uploaded model yet.</p>
              ) : (
                <div className="uw-sem-list">
                  {semanticsOptions.map((s) => (
                    <button
                      key={s}
                      type="button"
                      className={`uw-sem-opt${meta.semantics === s ? ' on' : ''}`}
                      onClick={() => setMeta({ ...meta, semantics: s })}
                    >
                      <span className="uw-sem-name mono">{prettySemantics(s)}</span>
                      <span className="uw-sem-desc">{SEMANTICS_COPY[s] || 'A semantics this deployment advertises.'}</span>
                    </button>
                  ))}
                </div>
              )}
            </div>

            <div className="uw-actions">
              <button className="btn btn-secondary btn-sm" onClick={onClose}>Cancel</button>
              <button className="btn btn-primary btn-sm" onClick={run} disabled={!canSubmit}>
                <Icon name="upload" size={14} /> Upload &amp; register
              </button>
            </div>
          </div>
        )}

        {(phase === 'running' || phase === 'error') && (
          <div className="uw-body">
            <ol className="uw-steps">
              {STAGES.map((s) => {
                const st = status[s.key];
                const cls = st === 'ok' ? 'ok' : st === 'run' ? 'run' : st === 'fail' ? 'fail' : 'idle';
                return (
                  <li key={s.key} className={`uw-step ${cls}`}>
                    <span className="uw-step-dot">
                      {st === 'ok' ? <Icon name="check" size={13} /> : st === 'fail' ? '×' : st === 'run' ? <span className="uw-spin" /> : ''}
                    </span>
                    <span className="uw-step-body">
                      <span className="uw-step-label">
                        {s.label}
                        {s.key === 'transfer' && st === 'run' && files.length > 1 && (
                          <span className="uw-step-count mono"> {transferred}/{files.length}</span>
                        )}
                      </span>
                      <span className="uw-step-note">{s.note}</span>
                    </span>
                  </li>
                );
              })}
            </ol>

            {failure && (
              <div className={`uw-refusal${failure.refusal ? ' reasoned' : ''}`}>
                <div className="uw-refusal-head">
                  <Icon name="alert" size={15} color={failure.refusal ? 'var(--crit)' : 'var(--warn)'} />
                  <span>{failure.refusal ? 'Refused, with a reason' : 'Could not complete'}</span>
                </div>
                <p className="uw-refusal-msg">{failure.msg}</p>
              </div>
            )}

            {phase === 'error' && (
              <div className="uw-actions">
                <button className="btn btn-secondary btn-sm" onClick={onClose}>Close</button>
                <button className="btn btn-primary btn-sm" onClick={() => { setStatus({}); setPhase('declare'); }}>Back</button>
              </div>
            )}
          </div>
        )}

        {phase === 'done' && result && (
          <div className="uw-body">
            <div className="uw-done">
              <div className="uw-done-mark"><Icon name="check" size={26} color="var(--live)" /></div>
              <h3 className="uw-done-title">{meta.name} is registered</h3>
              <p className="uw-desc">
                It passed structural admission, and this environment can serve what it declares. It is
                not live yet — deploy it from its drawer to run the load-and-score gate before it serves.
              </p>
              <div className="uw-actions center">
                <button className="btn btn-secondary btn-sm" onClick={onClose}>Done</button>
                <button className="btn btn-primary btn-sm" onClick={() => onRegistered?.(result)}>View model</button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default ModelUploadWizard;
