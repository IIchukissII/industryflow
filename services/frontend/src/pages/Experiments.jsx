// SPDX-FileCopyrightText: 2026 The IndustryFlow contributors
//
// SPDX-License-Identifier: AGPL-3.0-or-later

import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import AppShell from '../components/AppShell';
import { authFetch } from '../services/http';
import {
  ago, agoPhrase, duration, formatMetric, metricColumns, registeredByRun, runStatus, sortRuns,
} from '../utils/experiments';
import { experimentsReading } from '../utils/reading-experiments';
import './Experiments.css';

// The tenant's tracked experiments and their runs, read-only, through the session-scoped browse path
// (ADR-0019 dec 7): names arrive stripped of the tenant prefix, runs only through an experiment the
// tenant owns. The tracking server's own UI stays internal; runs are logged from notebooks.

const DOT = { live: 'ok', crit: 'bad', warn: 'pending', pending: 'pending', done: '' };

async function getJson(path) {
  const res = await authFetch(path);
  if (!res.ok) throw new Error(String(res.status));
  return res.json();
}

function RunDetail({ run }) {
  const params = Object.entries(run.params || {}).sort(([a], [b]) => a.localeCompare(b));
  const metrics = Object.entries(run.metrics || {}).sort(([a], [b]) => a.localeCompare(b));
  return (
    <div className="xp-detail">
      <div>
        <h3 className="xp-detail-h">Parameters</h3>
        {params.length ? (
          <dl className="xp-kv">
            {params.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd className="mono">{v}</dd></div>
            ))}
          </dl>
        ) : <p className="xp-dim">None logged.</p>}
      </div>
      <div>
        <h3 className="xp-detail-h">Metrics</h3>
        {metrics.length ? (
          <dl className="xp-kv">
            {metrics.map(([k, v]) => (
              <div key={k}><dt>{k}</dt><dd className="mono">{formatMetric(v)}</dd></div>
            ))}
          </dl>
        ) : <p className="xp-dim">None logged.</p>}
      </div>
      <p className="xp-runid mono">Run {run.run_id}</p>
    </div>
  );
}

function Runs({ name, registered }) {
  const [state, setState] = useState({ loading: true, error: null, runs: [] });
  const [sort, setSort] = useState(null);
  const [open, setOpen] = useState(null);

  const load = useCallback(async () => {
    setState({ loading: true, error: null, runs: [] });
    setOpen(null);
    try {
      const d = await getJson(`/api/experiments/${encodeURIComponent(name)}/runs`);
      setState({ loading: false, error: null, runs: d.runs || [] });
    } catch (e) {
      setState({ loading: false, error: e.message, runs: [] });
    }
  }, [name]);

  // Invoked from an inner async function, so the effect body starts no synchronous state update (#214).
  useEffect(() => { (async () => { await load(); })(); }, [load]);

  const columns = useMemo(() => metricColumns(state.runs), [state.runs]);
  const runs = useMemo(() => sortRuns(state.runs, sort), [state.runs, sort]);
  const newest = state.runs.reduce((m, r) => Math.max(m, r.start_time || 0), 0);

  const sortBy = (key) => setSort((s) => (s?.key === key
    ? { key, dir: s.dir === 'desc' ? 'asc' : 'desc' }
    : { key, dir: 'desc' }));
  const ariaSort = (key) => {
    if (sort?.key === key) return sort.dir === 'asc' ? 'ascending' : 'descending';
    return key === 'start_time' && !sort ? 'descending' : 'none';
  };

  return (
    <section className="xp-runs" aria-labelledby="xp-runs-h">
      <header className="xp-runs-head">
        <h2 id="xp-runs-h" className="xp-title">{name}</h2>
        {!state.loading && !state.error && (
          <p className="xp-facts">
            {state.runs.length === 1 ? '1 run' : `${state.runs.length} runs`}
            {newest ? `, the latest started ${agoPhrase(newest)}` : ''}
          </p>
        )}
      </header>

      {state.loading && <p className="xp-state"><span className="sdot pending" /> Loading runs</p>}
      {state.error && (
        <div className="xp-state xp-state-bad" role="alert">
          <p>The runs of {name} could not be read{state.error === '502' ? ': experiment tracking is unavailable' : ''}.</p>
          <button type="button" className="btn" onClick={load}>Try again</button>
        </div>
      )}
      {!state.loading && !state.error && !state.runs.length && (
        <p className="xp-dim">This experiment has no runs.</p>
      )}

      {!state.loading && !state.error && state.runs.length > 0 && (
        <div className="xp-table-wrap">
          <table className="data-table xp-table">
            <thead>
              <tr>
                <th scope="col">Run</th>
                <th scope="col">Status</th>
                <th scope="col" aria-sort={ariaSort('start_time')}>
                  <button type="button" className="xp-sort" onClick={() => sortBy('start_time')}>Started</button>
                </th>
                <th scope="col" className="xp-num">Duration</th>
                {columns.map((k) => (
                  <th scope="col" key={k} className="xp-num" aria-sort={ariaSort(k)}>
                    <button type="button" className="xp-sort mono" onClick={() => sortBy(k)}>{k}</button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => {
                const st = runStatus(r.status);
                const isOpen = open === r.run_id;
                const model = registered[r.run_id];
                return (
                  <Fragment key={r.run_id}>
                    <tr className={`xp-row${isOpen ? ' xp-row-open' : ''}`}>
                      <td>
                        <button
                          type="button" className="xp-run" aria-expanded={isOpen} title={r.run_name || r.run_id}
                          onClick={() => setOpen(isOpen ? null : r.run_id)}
                        >
                          {r.run_name || <span className="mono">{r.run_id.slice(0, 8)}</span>}
                        </button>
                        {model && (
                          <Link className="xp-model" to="/ml-models" title={`Registered as ${model}`}>
                            Registered as {model}
                          </Link>
                        )}
                      </td>
                      <td><span className="xp-status"><span className={`sdot ${DOT[st.tone]}`} />{st.label}</span></td>
                      <td className="xp-dim xp-when">{ago(r.start_time)}</td>
                      <td className="xp-num mono">{duration(r) || (r.status === 'RUNNING' ? 'running' : '')}</td>
                      {columns.map((k) => <td key={k} className="xp-num mono">{formatMetric(r.metrics?.[k])}</td>)}
                    </tr>
                    {isOpen && (
                      <tr className="xp-detail-row">
                        <td colSpan={4 + columns.length}><RunDetail run={r} /></td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export default function Experiments({ user }) {
  const [params, setParams] = useSearchParams();
  const [state, setState] = useState({ loading: true, error: null, experiments: [], models: [] });

  const load = useCallback(async () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    const [exp, reg] = await Promise.allSettled([
      getJson('/api/experiments'),
      getJson('/api/registered-models'),
    ]);
    if (exp.status === 'rejected') {
      setState({ loading: false, error: exp.reason.message, experiments: [], models: [] });
      return;
    }
    const experiments = [...(exp.value.experiments || [])]
      .sort((a, b) => (b.last_update_time || 0) - (a.last_update_time || 0));
    // The registry only marks which runs became models; the page reads without it.
    const models = reg.status === 'fulfilled' ? reg.value.models || [] : [];
    setState({ loading: false, error: null, experiments, models });
  }, []);

  // Invoked from an inner async function, so the effect body starts no synchronous state update (#214).
  useEffect(() => { (async () => { await load(); })(); }, [load]);

  const registered = useMemo(() => registeredByRun(state.models), [state.models]);
  const wanted = params.get('experiment');
  const selected = state.experiments.find((e) => e.name === wanted) || state.experiments[0];
  const select = (name) => setParams({ experiment: name });

  let body;
  if (state.loading && !state.experiments.length) {
    body = <p className="xp-state"><span className="sdot pending" /> Loading experiments</p>;
  } else if (state.error) {
    body = (
      <div className="xp-state xp-state-bad" role="alert">
        <p>{state.error === '502' ? 'Experiment tracking is unavailable.' : 'Experiments could not be read.'}</p>
        <button type="button" className="btn" onClick={load}>Try again</button>
      </div>
    );
  } else if (!state.experiments.length) {
    body = null;
  } else {
    body = (
      <div className="xp-body">
        <nav className="xp-list" aria-label="Experiments">
          {state.experiments.map((e) => (
            <button
              type="button" key={e.name}
              className="xp-item" aria-current={e.name === selected.name ? 'true' : undefined}
              onClick={() => select(e.name)}
            >
              <span className="xp-item-name">{e.name}</span>
              {e.last_update_time ? <span className="xp-item-when">{ago(e.last_update_time)}</span> : null}
            </button>
          ))}
        </nav>
        <Runs key={selected.name} name={selected.name} registered={registered} />
      </div>
    );
  }

  const reading = state.loading && !state.experiments.length
    ? 'Reading the tracked experiments.'
    : state.error
      ? 'The tracked experiments could not be read.'
      : experimentsReading({ experiments: state.experiments, registered: Object.keys(registered).length });

  return (
    <AppShell user={user} title="Experiments">
      <div className="xp">
        <div className="xp-head">
          <p className="reading">{reading}</p>
          <div className="xp-head-actions">
            {!state.loading && !state.error && !state.experiments.length && (
              <Link className="btn btn-primary" to="/notebooks">Open notebooks</Link>
            )}
            <button type="button" className="btn" onClick={load} disabled={state.loading}>Refresh</button>
          </div>
        </div>
        {body}
      </div>
    </AppShell>
  );
}
